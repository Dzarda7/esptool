/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include "esf_port.h"
#include "serial.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ------------------------------------------------------------------ */
/*  Platform-specific monotonic clock and sleep                        */

#ifdef _WIN32
#  define WIN32_LEAN_AND_MEAN
#  include <windows.h>
static uint64_t mono_ms(void)   { return (uint64_t)GetTickCount64(); }
static void     delay_ms(uint32_t ms) { Sleep(ms); }
#else
#  include <time.h>
#  include <unistd.h>
static uint64_t mono_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}
static void delay_ms(uint32_t ms) { usleep((useconds_t)ms * 1000u); }
#endif

/* ------------------------------------------------------------------ */
/*  Timing constants (overridable via -D at build time)                */

#ifndef SERIAL_FLASHER_RESET_HOLD_TIME_MS
#define SERIAL_FLASHER_RESET_HOLD_TIME_MS 100
#endif
#ifndef SERIAL_FLASHER_BOOT_HOLD_TIME_MS
#define SERIAL_FLASHER_BOOT_HOLD_TIME_MS  50
#endif
#define PORT_REOPEN_TIMEOUT_MS 3000

/* ------------------------------------------------------------------ */
/*  USB JTAG Serial port re-enumeration                                */

/*
 * After a USB JTAG Serial reset the device re-enumerates and its port
 * node disappears briefly.  Close the current fd, then poll serial_open()
 * until the port reappears or the timeout expires.
 */
static esp_loader_error_t esf_wait_port_reopen(esf_port_t *p,
                                                uint32_t timeout_ms)
{
    serial_close(p->_serial);
    p->_serial = NULL;

    uint64_t deadline = mono_ms() + timeout_ms;

    while (mono_ms() < deadline) {
        delay_ms(100);
        if (serial_open(&p->_serial, p->device) == 0 &&
            serial_set_baud(p->_serial, p->baudrate) == 0)
            return ESP_LOADER_SUCCESS;
        if (p->_serial) {
            serial_close(p->_serial);
            p->_serial = NULL;
        }
    }

    fprintf(stderr, "esf_port: timed out waiting for %s to reappear\n",
            p->device);
    return ESP_LOADER_ERROR_TIMEOUT;
}

/* ------------------------------------------------------------------ */
/*  init / deinit                                                       */

static esp_loader_error_t port_init(esp_loader_port_t *port)
{
    esf_port_t *p = container_of(port, esf_port_t, port);

    p->_time_end    = 0;
    p->_is_usb_jtag = 0;

    if (serial_open(&p->_serial, p->device) != 0) {
        fprintf(stderr, "esf_port: cannot open %s\n", p->device);
        return ESP_LOADER_ERROR_FAIL;
    }

    if (serial_set_baud(p->_serial, p->baudrate) != 0) {
        fprintf(stderr, "esf_port: cannot set baud %u on %s\n",
                p->baudrate, p->device);
        serial_close(p->_serial);
        p->_serial = NULL;
        return ESP_LOADER_ERROR_FAIL;
    }

    unsigned int vid = 0, pid = 0;
    p->_is_usb_jtag = (serial_get_usb_id(p->device, &vid, &pid) == 0 &&
                       vid == 0x303A && pid == 0x1001);
    return ESP_LOADER_SUCCESS;
}

static void port_deinit(esp_loader_port_t *port)
{
    esf_port_t *p = container_of(port, esf_port_t, port);
    serial_close(p->_serial);
    p->_serial = NULL;
}

/* ------------------------------------------------------------------ */
/*  I/O                                                                 */

static esp_loader_error_t port_write(esp_loader_port_t *port,
                                     const uint8_t *data,
                                     uint16_t size, uint32_t timeout)
{
    esf_port_t *p = container_of(port, esf_port_t, port);
    (void)timeout; /* deadline is owned by start_timer / remaining_time */
    return (serial_write(p->_serial, data, size) < 0)
               ? ESP_LOADER_ERROR_FAIL
               : ESP_LOADER_SUCCESS;
}

static esp_loader_error_t port_read(esp_loader_port_t *port,
                                    uint8_t *data,
                                    uint16_t size, uint32_t timeout)
{
    esf_port_t *p = container_of(port, esf_port_t, port);
    (void)timeout;

    for (uint16_t i = 0; i < size; i++) {
        int64_t remaining = p->_time_end - (int64_t)mono_ms();
        unsigned ms = (remaining > 0) ? (unsigned)remaining : 0u;
        ssize_t n = serial_read(p->_serial, &data[i], 1, ms);
        if (n == 0)
            return ESP_LOADER_ERROR_TIMEOUT;
        if (n < 0)
            return ESP_LOADER_ERROR_FAIL;
    }
    return ESP_LOADER_SUCCESS;
}

/* ------------------------------------------------------------------ */
/*  Timer                                                               */

static void port_start_timer(esp_loader_port_t *port, uint32_t ms)
{
    esf_port_t *p = container_of(port, esf_port_t, port);
    p->_time_end = (int64_t)mono_ms() + (int64_t)ms;
}

static uint32_t port_remaining_time(esp_loader_port_t *port)
{
    esf_port_t *p = container_of(port, esf_port_t, port);
    int64_t remaining = p->_time_end - (int64_t)mono_ms();
    return (remaining > 0) ? (uint32_t)remaining : 0u;
}

static void port_delay_ms(esp_loader_port_t *port, uint32_t ms)
{
    (void)port;
    delay_ms(ms);
}

/* ------------------------------------------------------------------ */
/*  Baud rate                                                           */

static esp_loader_error_t port_change_rate(esp_loader_port_t *port,
                                           uint32_t rate)
{
    esf_port_t *p = container_of(port, esf_port_t, port);
    return (serial_set_baud(p->_serial, (unsigned)rate) == 0)
               ? ESP_LOADER_SUCCESS
               : ESP_LOADER_ERROR_FAIL;
}

/* ------------------------------------------------------------------ */
/*  Reset sequences                                                     */

static void port_reset_target(esp_loader_port_t *port)
{
    esf_port_t *p = container_of(port, esf_port_t, port);

    /* Hard reset: pulse RESET with BOOT deasserted so the chip runs the
     * application, not the bootloader.  Both lines are driven together so
     * the bridge sees a clean edge.  USB JTAG Serial devices re-enumerate
     * after reset; wait for the port to reappear. */
    serial_set_dtr_rts(p->_serial, 0, 1); /* BOOT=high  RESET=low  */
    delay_ms(SERIAL_FLASHER_RESET_HOLD_TIME_MS);
    serial_set_dtr_rts(p->_serial, 0, 0); /* BOOT=high  RESET=high */

    if (p->_is_usb_jtag)
        esf_wait_port_reopen(p, PORT_REOPEN_TIMEOUT_MS);
}

static void port_enter_bootloader(esp_loader_port_t *port)
{
    esf_port_t *p = container_of(port, esf_port_t, port);

    if (p->_is_usb_jtag) {
        /* USBJTAGSerialReset — required for chips that expose their own
         * USB JTAG/Serial peripheral (ESP32-C3, S3, C6, H2, P4).
         *
         * All steps use serial_set_dtr_rts() so both lines change in one
         * driver call, giving a clean edge even through the USB frame.
         *
         * Convention (standard inverting circuit):
         *   DTR=1 → BOOT pin LOW   (enter bootloader)
         *   RTS=1 → RESET pin LOW  (chip held in reset)
         *
         *  idle      → (0,0)
         *  BOOT low  → (1,0)  assert BOOT before reset
         *  RESET low → (1,1)  chip in reset, BOOT already latched
         *  rel BOOT  → (0,1)  through (0,1) avoids (1,1)→(0,0) glitch
         *  rel RESET → (0,0)  chip exits reset → ROM sees BOOT=low → bootloader
         */
        serial_set_dtr_rts(p->_serial, 0, 0);
        delay_ms(SERIAL_FLASHER_RESET_HOLD_TIME_MS);
        serial_set_dtr_rts(p->_serial, 1, 0);
        delay_ms(SERIAL_FLASHER_RESET_HOLD_TIME_MS);
        serial_set_dtr_rts(p->_serial, 1, 1);
        serial_set_dtr_rts(p->_serial, 0, 1);
        delay_ms(SERIAL_FLASHER_RESET_HOLD_TIME_MS);
        serial_set_dtr_rts(p->_serial, 0, 0);
        esf_wait_port_reopen(p, PORT_REOPEN_TIMEOUT_MS);
    } else {
        /* Standard auto-reset circuit (CP2102, CH340, FT232, …).
         *
         * serial_set_dtr_rts() is atomic on POSIX (single TIOCMSET ioctl)
         * and driver-batched on Windows, so this one sequence covers both
         * ClassicReset and UnixTightReset from esptool — no separate paths
         * are needed.
         *
         * The through-(1,1) step prevents a (0,0)→(0,1) intermediate state
         * that would briefly deassert BOOT before RESET goes low, causing
         * the ROM to latch the wrong boot mode.
         *
         *  idle         → (0,0)
         *  via (1,1)    → (1,1)  transition through known state
         *  RESET low    → (0,1)  BOOT=high, RESET=low — chip held in reset
         *  BOOT low     → (1,0)  RESET released with BOOT asserted → bootloader
         *  release      → (0,0)  BOOT released, chip runs in bootloader
         */
        serial_set_dtr_rts(p->_serial, 0, 0);
        serial_set_dtr_rts(p->_serial, 1, 1);
        serial_set_dtr_rts(p->_serial, 0, 1);
        delay_ms(SERIAL_FLASHER_RESET_HOLD_TIME_MS);
        serial_set_dtr_rts(p->_serial, 1, 0);
        delay_ms(SERIAL_FLASHER_BOOT_HOLD_TIME_MS);
        serial_set_dtr_rts(p->_serial, 0, 0);
        serial_flush_input(p->_serial);
    }
}

/* ------------------------------------------------------------------ */
/*  Port probing                                                        */

char *esf_find_esp_port(void)
{
    char **ports;
    int n = serial_list_ports(&ports);
    if (n <= 0)
        return NULL;

    char *found = NULL;
    for (int i = 0; ports[i] && !found; i++) {
        fprintf(stderr, "  Trying %s...", ports[i]);
        fflush(stderr);

        esf_port_t sport = {
            .port.ops = &esf_port_ops,
            .device   = ports[i],
            .baudrate = 115200,
        };

        esp_loader_t probe;
        if (esp_loader_init_uart(&probe, &sport.port) != ESP_LOADER_SUCCESS) {
            fprintf(stderr, " open failed\n");
            continue;
        }

        esp_loader_connect_args_t ca = ESP_LOADER_CONNECT_DEFAULT();
        if (esp_loader_connect(&probe, &ca) == ESP_LOADER_SUCCESS) {
            fprintf(stderr, " found ESP chip\n");
            esp_loader_reset_target(&probe);
            size_t len = strlen(ports[i]);
            found = malloc(len + 1);
            if (found)
                memcpy(found, ports[i], len + 1);
        } else {
            fprintf(stderr, " no response\n");
        }
        esp_loader_deinit(&probe);
    }

    serial_free_port_list(ports);
    return found;
}

/* ------------------------------------------------------------------ */
/*  Vtable                                                              */

const esp_loader_port_ops_t esf_port_ops = {
    .init                     = port_init,
    .deinit                   = port_deinit,
    .enter_bootloader         = port_enter_bootloader,
    .reset_target             = port_reset_target,
    .start_timer              = port_start_timer,
    .remaining_time           = port_remaining_time,
    .delay_ms                 = port_delay_ms,
    .debug_print              = NULL,
    .change_transmission_rate = port_change_rate,
    .write                    = port_write,
    .read                     = port_read,
    .spi_set_cs               = NULL,
    .sdio_write               = NULL,
    .sdio_read                = NULL,
    .sdio_card_init           = NULL,
};
