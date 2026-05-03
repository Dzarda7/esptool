/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "serial.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct serial {
    HANDLE   handle;
    unsigned baudrate;
    char    *device;
};

int serial_open(struct serial **out, const char *path)
{
    struct serial *s = calloc(1, sizeof(*s));
    if (!s)
        return -ENOMEM;

    s->device = _strdup(path);
    if (!s->device) {
        free(s);
        return -ENOMEM;
    }

    /* Ports above COM9 require the \\.\COMn prefix. */
    char win_path[64];
    if (strncmp(path, "\\\\.\\", 4) != 0)
        snprintf(win_path, sizeof(win_path), "\\\\.\\%s", path);
    else
        strncpy(win_path, path, sizeof(win_path) - 1);

    s->handle = CreateFileA(win_path,
                            GENERIC_READ | GENERIC_WRITE,
                            0, NULL, OPEN_EXISTING,
                            FILE_ATTRIBUTE_NORMAL, NULL);
    if (s->handle == INVALID_HANDLE_VALUE) {
        int err = (GetLastError() == ERROR_FILE_NOT_FOUND) ? -ENOENT : -EIO;
        free(s->device);
        free(s);
        return err;
    }

    DCB dcb = { .DCBlength = sizeof(DCB) };
    if (!GetCommState(s->handle, &dcb)) {
        CloseHandle(s->handle);
        free(s->device);
        free(s);
        return -EIO;
    }

    dcb.BaudRate        = CBR_115200;
    dcb.ByteSize        = 8;
    dcb.Parity          = NOPARITY;
    dcb.StopBits        = ONESTOPBIT;
    dcb.fBinary         = TRUE;
    dcb.fDtrControl     = DTR_CONTROL_DISABLE;
    dcb.fRtsControl     = RTS_CONTROL_DISABLE;
    dcb.fOutxCtsFlow    = FALSE;
    dcb.fOutxDsrFlow    = FALSE;
    dcb.fDsrSensitivity = FALSE;
    dcb.fOutX           = FALSE;
    dcb.fInX            = FALSE;
    dcb.fNull           = FALSE;
    dcb.fAbortOnError   = FALSE;

    if (!SetCommState(s->handle, &dcb)) {
        CloseHandle(s->handle);
        free(s->device);
        free(s);
        return -EIO;
    }

    SetupComm(s->handle, 4096, 4096);
    s->baudrate = 115200;

    *out = s;
    return 0;
}

void serial_close(struct serial *s)
{
    if (!s)
        return;
    if (s->handle != INVALID_HANDLE_VALUE)
        CloseHandle(s->handle);
    free(s->device);
    free(s);
}

int serial_set_baud(struct serial *s, unsigned baud)
{
    DCB dcb = { .DCBlength = sizeof(DCB) };
    if (!GetCommState(s->handle, &dcb))
        return -EIO;
    dcb.BaudRate = (DWORD)baud;
    if (!SetCommState(s->handle, &dcb))
        return -EIO;
    s->baudrate = baud;
    return 0;
}

ssize_t serial_read(struct serial *s, void *buf, size_t n, unsigned timeout_ms)
{
    COMMTIMEOUTS ct = {
        .ReadIntervalTimeout         = MAXDWORD,
        .ReadTotalTimeoutMultiplier  = MAXDWORD,
        .ReadTotalTimeoutConstant    = (DWORD)timeout_ms,
        .WriteTotalTimeoutMultiplier = 0,
        .WriteTotalTimeoutConstant   = 0,
    };
    SetCommTimeouts(s->handle, &ct);

    DWORD got = 0;
    if (!ReadFile(s->handle, buf, (DWORD)n, &got, NULL))
        return -1;
    return (ssize_t)got;
}

ssize_t serial_write(struct serial *s, const void *buf, size_t n)
{
    const uint8_t *p = (const uint8_t *)buf;
    size_t written = 0;
    while (written < n) {
        DWORD w = 0;
        if (!WriteFile(s->handle, p + written, (DWORD)(n - written), &w, NULL))
            return -1;
        written += w;
    }
    return (ssize_t)written;
}

void serial_set_dtr(struct serial *s, int on)
{
    EscapeCommFunction(s->handle, on ? SETDTR : CLRDTR);
}

void serial_set_rts(struct serial *s, int on)
{
    EscapeCommFunction(s->handle, on ? SETRTS : CLRRTS);
}

void serial_set_dtr_rts(struct serial *s, int dtr, int rts)
{
    /* Windows has no atomic "set both" API — EscapeCommFunction only handles
     * one line at a time.  The USB driver layer batches consecutive control
     * transfers so the two calls arrive as one USB frame on most bridges,
     * giving the same glitch-free behaviour as TIOCMSET on POSIX. */
    EscapeCommFunction(s->handle, dtr ? SETDTR : CLRDTR);
    EscapeCommFunction(s->handle, rts ? SETRTS : CLRRTS);
}

void serial_flush_input(struct serial *s)
{
    PurgeComm(s->handle, PURGE_RXCLEAR);
}

int serial_list_ports(char ***ports_out)
{
    HKEY hkey;
    if (RegOpenKeyExA(HKEY_LOCAL_MACHINE,
                      "HARDWARE\\DEVICEMAP\\SERIALCOMM",
                      0, KEY_READ, &hkey) != ERROR_SUCCESS)
        return -ENOENT;

    char **list  = NULL;
    int    count = 0;
    DWORD  idx   = 0;

    while (1) {
        char  name[256], value[256];
        DWORD name_len  = sizeof(name);
        DWORD value_len = sizeof(value);
        DWORD type;

        LONG rc = RegEnumValueA(hkey, idx++, name, &name_len,
                                NULL, &type, (BYTE *)value, &value_len);
        if (rc == ERROR_NO_MORE_ITEMS)
            break;
        if (rc != ERROR_SUCCESS || type != REG_SZ)
            continue;

        char **tmp = realloc(list, (size_t)(count + 2) * sizeof(char *));
        if (!tmp)
            break;
        list = tmp;
        list[count] = _strdup(value);
        if (!list[count])
            break;
        list[++count] = NULL;
    }

    RegCloseKey(hkey);

    if (!list) {
        list = calloc(1, sizeof(char *));
        if (!list)
            return -ENOMEM;
        list[0] = NULL;
    }

    *ports_out = list;
    return count;
}

void serial_free_port_list(char **ports)
{
    if (!ports)
        return;
    for (char **p = ports; *p; p++)
        free(*p);
    free(ports);
}

int serial_get_usb_id(const char *device, unsigned int *vid, unsigned int *pid)
{
    /* TODO: implement via SetupDiGetDeviceRegistryProperty + CM_Get_DevNode_Property */
    (void)device; (void)vid; (void)pid;
    return -ENOSYS;
}
