/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file serial.h
 * @brief Small portable serial-port API for the ESPLoader protocol.
 *
 * Supports 8N1 serial without flow control.
 * Platform code lives in platform/{posix,win}/serial.c.
 */

#ifndef ESF_SERIAL_H
#define ESF_SERIAL_H

#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#  include <BaseTsd.h>
typedef SSIZE_T ssize_t;
#else
#  include <sys/types.h>
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Opaque handle — definition is platform-specific. */
struct serial;

/**
 * Open a serial device in 8N1 raw mode (no echo, no flow control).
 * @param out  Receives the allocated handle on success.
 * @param path Device path, e.g. "/dev/ttyUSB0" or "COM3".
 * @return 0 on success, negative errno on failure.
 */
int serial_open(struct serial **out, const char *path);

/**
 * Close and free a serial handle. Safe to call on NULL.
 */
void serial_close(struct serial *s);

/**
 * Change the baud rate of an open port.
 * @return 0 on success, negative errno on failure.
 */
int serial_set_baud(struct serial *s, unsigned baud);

/**
 * Read up to @p n bytes with a millisecond timeout.
 * @return Bytes read (>0), 0 on timeout, -1 on error (errno set).
 */
ssize_t serial_read(struct serial *s, void *buf, size_t n, unsigned timeout_ms);

/**
 * Write @p n bytes, retrying on partial writes and EINTR.
 * @return Bytes written on success, -1 on error (errno set).
 */
ssize_t serial_write(struct serial *s, const void *buf, size_t n);

/**
 * Set the DTR modem control line.
 * @param on 1 to assert, 0 to deassert.
 */
void serial_set_dtr(struct serial *s, int on);

/**
 * Set the RTS modem control line.
 * @param on 1 to assert, 0 to deassert.
 */
void serial_set_rts(struct serial *s, int on);

/**
 * Set both DTR and RTS atomically.
 *
 * On POSIX uses a single TIOCMSET ioctl; on Windows relies on the USB
 * driver batching consecutive EscapeCommFunction calls into one USB frame.
 * Use this in all reset sequences to avoid glitches on USB-UART bridges.
 */
void serial_set_dtr_rts(struct serial *s, int dtr, int rts);

/**
 * Discard all bytes waiting in the receive buffer.
 */
void serial_flush_input(struct serial *s);

/**
 * Enumerate available serial ports.
 * @param ports_out Receives a NULL-terminated array of heap-allocated strings.
 * @return Number of ports found (>=0), or negative errno on failure.
 */
int serial_list_ports(char ***ports_out);

/**
 * Free a port list returned by serial_list_ports().
 */
void serial_free_port_list(char **ports);

/**
 * Read the USB vendor and product IDs for a serial device from the OS.
 * Currently implemented on Linux only; returns -ENOSYS elsewhere.
 * @return 0 on success, negative errno on failure.
 */
int serial_get_usb_id(const char *device, unsigned int *vid, unsigned int *pid);

#ifdef __cplusplus
}
#endif

#endif /* ESF_SERIAL_H */
