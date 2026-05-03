/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file esf_port.h
 * @brief esp-serial-flasher port backed by the esp-ice serial API.
 *
 * Usage:
 * @code
 *   esf_port_t p = {
 *       .port.ops = &esf_port_ops,
 *       .device   = "/dev/ttyUSB0",
 *       .baudrate = 115200,
 *   };
 *   esp_loader_t loader;
 *   esp_loader_init_uart(&loader, &p.port);
 * @endcode
 */

#ifndef ESF_PORT_H
#define ESF_PORT_H

#include "esp_loader.h"
#include "esp_loader_io.h"

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Opaque serial handle — definition is platform-specific. */
struct serial;

/**
 * Concrete port instance. Embed as the first step before calling
 * esp_loader_init_uart(); container_of recovers it inside every callback.
 */
typedef struct {
    esp_loader_port_t  port;      /**< Must be first — pass &port to init */

    /* Public: fill before calling esp_loader_init_uart() */
    const char *device;           /**< e.g. "/dev/ttyUSB0" or "COM3"      */
    unsigned    baudrate;         /**< Initial baud rate, e.g. 115200      */

    /* Private runtime state — do not access directly */
    struct serial *_serial;       /**< Opened by init, closed by deinit    */
    int64_t        _time_end;     /**< Deadline set by start_timer         */
    int            _is_usb_jtag;  /**< 1 when VID=0x303A PID=0x1001       */
} esf_port_t;

/** Operations vtable — assign to port.ops before calling init. */
extern const esp_loader_port_ops_t esf_port_ops;

/**
 * Probe all available serial ports and return the path of the first one
 * that responds as an ESP chip.  The device is reset to run mode before
 * returning.  Returns a heap-allocated string the caller must free(), or
 * NULL if no ESP chip was found.
 */
char *esf_find_esp_port(void);

#ifdef __cplusplus
}
#endif

#endif /* ESF_PORT_H */
