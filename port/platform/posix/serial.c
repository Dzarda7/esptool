/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include "serial.h"

#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <termios.h>
#include <unistd.h>

#ifdef __APPLE__
#  include <IOKit/serial/ioss.h>  /* IOSSIOSPEED for non-standard rates */
#endif

struct serial {
    int      fd;
    unsigned baudrate;
    char    *device;
};

/* Map numeric baud rate to termios speed constant, or B0 if unknown. */
static speed_t baud_to_speed(unsigned baud)
{
    static const struct { unsigned baud; speed_t speed; } table[] = {
        {    9600,    B9600   },
        {   19200,   B19200  },
        {   38400,   B38400  },
        {   57600,   B57600  },
        {  115200,  B115200  },
        {  230400,  B230400  },
#ifdef B460800
        {  460800,  B460800  },
#endif
#ifdef B921600
        {  921600,  B921600  },
#endif
#ifdef B1500000
        { 1500000, B1500000  },
#endif
#ifdef B2000000
        { 2000000, B2000000  },
#endif
    };
    for (size_t i = 0; i < sizeof(table) / sizeof(table[0]); i++)
        if (table[i].baud == baud)
            return table[i].speed;
    return B0;
}

int serial_open(struct serial **out, const char *path)
{
    struct serial *s = calloc(1, sizeof(*s));
    if (!s)
        return -ENOMEM;

    s->device = strdup(path);
    if (!s->device) {
        free(s);
        return -ENOMEM;
    }

    /* O_NONBLOCK prevents open() from blocking if carrier is not present. */
    s->fd = open(path, O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (s->fd < 0) {
        int err = -errno;
        free(s->device);
        free(s);
        return err;
    }

    /* Switch back to blocking I/O after open succeeds. */
    int flags = fcntl(s->fd, F_GETFL);
    fcntl(s->fd, F_SETFL, flags & ~O_NONBLOCK);

    struct termios tty;
    if (tcgetattr(s->fd, &tty) != 0) {
        int err = -errno;
        close(s->fd);
        free(s->device);
        free(s);
        return err;
    }

    cfmakeraw(&tty);
    tty.c_cflag |= (CLOCAL | CREAD);
    tty.c_cflag &= ~CRTSCTS;
    tty.c_cc[VMIN]  = 0;
    tty.c_cc[VTIME] = 0;

    cfsetispeed(&tty, B115200);
    cfsetospeed(&tty, B115200);
    s->baudrate = 115200;

    if (tcsetattr(s->fd, TCSANOW, &tty) != 0) {
        int err = -errno;
        close(s->fd);
        free(s->device);
        free(s);
        return err;
    }

    *out = s;
    return 0;
}

void serial_close(struct serial *s)
{
    if (!s)
        return;
    if (s->fd >= 0)
        close(s->fd);
    free(s->device);
    free(s);
}

int serial_set_baud(struct serial *s, unsigned baud)
{
    struct termios tty;
    if (tcgetattr(s->fd, &tty) != 0)
        return -errno;

    speed_t sp = baud_to_speed(baud);
    if (sp != B0) {
        cfsetispeed(&tty, sp);
        cfsetospeed(&tty, sp);
        if (tcsetattr(s->fd, TCSANOW, &tty) != 0)
            return -errno;
    } else {
#ifdef __APPLE__
        speed_t speed = (speed_t)baud;
        if (ioctl(s->fd, IOSSIOSPEED, &speed) != 0)
            return -errno;
#else
        return -EINVAL;
#endif
    }

    s->baudrate = baud;
    return 0;
}

ssize_t serial_read(struct serial *s, void *buf, size_t n, unsigned timeout_ms)
{
    fd_set fds;
    FD_ZERO(&fds);
    FD_SET(s->fd, &fds);

    struct timeval tv = {
        .tv_sec  = timeout_ms / 1000,
        .tv_usec = (timeout_ms % 1000) * 1000,
    };

    int r = select(s->fd + 1, &fds, NULL, NULL, &tv);
    if (r < 0)
        return -1;
    if (r == 0)
        return 0;  /* timeout */

    return read(s->fd, buf, n);
}

ssize_t serial_write(struct serial *s, const void *buf, size_t n)
{
    const uint8_t *p = (const uint8_t *)buf;
    size_t written = 0;
    while (written < n) {
        ssize_t r = write(s->fd, p + written, n - written);
        if (r < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        written += (size_t)r;
    }
    return (ssize_t)written;
}

static void set_modem_bit(struct serial *s, int bit, int on)
{
    /* Ignore errors: ENOTTY/EINVAL means the port doesn't support modem lines. */
    ioctl(s->fd, on ? TIOCMBIS : TIOCMBIC, &bit);
}

void serial_set_dtr(struct serial *s, int on)
{
    set_modem_bit(s, TIOCM_DTR, on);
}

void serial_set_rts(struct serial *s, int on)
{
    set_modem_bit(s, TIOCM_RTS, on);
}

void serial_set_dtr_rts(struct serial *s, int dtr, int rts)
{
    int bits;
    if (ioctl(s->fd, TIOCMGET, &bits) < 0)
        return;

    if (dtr) bits |= TIOCM_DTR; else bits &= ~TIOCM_DTR;
    if (rts) bits |= TIOCM_RTS; else bits &= ~TIOCM_RTS;

    ioctl(s->fd, TIOCMSET, &bits);
}

void serial_flush_input(struct serial *s)
{
    tcflush(s->fd, TCIFLUSH);
}

int serial_list_ports(char ***ports_out)
{
    static const char *const patterns[] = {
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
#ifdef __APPLE__
        "/dev/tty.usbserial*",
        "/dev/tty.usbmodem*",
        "/dev/tty.SLAB_USBtoUART*",
        "/dev/tty.wchusbserial*",
#endif
        NULL,
    };

    glob_t gl = { 0 };
    int gl_flags = GLOB_ERR;

    for (int i = 0; patterns[i]; i++) {
        glob(patterns[i],
             (i == 0) ? gl_flags : (gl_flags | GLOB_APPEND),
             NULL, &gl);
    }

    size_t total = gl.gl_pathc;
    char **list = calloc(total + 1, sizeof(char *));
    if (!list) {
        globfree(&gl);
        return -ENOMEM;
    }

    for (size_t i = 0; i < total; i++) {
        list[i] = strdup(gl.gl_pathv[i]);
        if (!list[i]) {
            for (size_t j = 0; j < i; j++)
                free(list[j]);
            free(list);
            globfree(&gl);
            return -ENOMEM;
        }
    }
    list[total] = NULL;
    globfree(&gl);

    *ports_out = list;
    return (int)total;
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
#ifdef __linux__
    const char *base = strrchr(device, '/');
    base = base ? base + 1 : device;

    char path[256];
    snprintf(path, sizeof(path),
             "/sys/class/tty/%s/device/../idVendor", base);
    FILE *f = fopen(path, "r");
    if (!f)
        return -ENOENT;
    if (fscanf(f, "%x", vid) != 1) {
        fclose(f);
        return -EIO;
    }
    fclose(f);

    snprintf(path, sizeof(path),
             "/sys/class/tty/%s/device/../idProduct", base);
    f = fopen(path, "r");
    if (!f)
        return -ENOENT;
    if (fscanf(f, "%x", pid) != 1) {
        fclose(f);
        return -EIO;
    }
    fclose(f);
    return 0;
#else
    (void)device; (void)vid; (void)pid;
    return -ENOSYS;
#endif
}
