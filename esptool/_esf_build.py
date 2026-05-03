"""
cffi API-mode build script for the esp-serial-flasher C extension.

Run directly to build in-place:
    python esptool/_esf_build.py

Or invoked automatically by setuptools via the cffi_modules entry in
pyproject.toml when building the package.
"""

import os
import sys

from cffi import FFI

ffi = FFI()

ffi.cdef("""
    /* ---- error codes ------------------------------------------------ */
    typedef enum {
        ESP_LOADER_SUCCESS             = 0,
        ESP_LOADER_ERROR_FAIL,
        ESP_LOADER_ERROR_TIMEOUT,
        ESP_LOADER_ERROR_IMAGE_SIZE,
        ESP_LOADER_ERROR_INVALID_MD5,
        ESP_LOADER_ERROR_INVALID_PARAM,
        ESP_LOADER_ERROR_INVALID_TARGET,
        ESP_LOADER_ERROR_UNSUPPORTED_CHIP,
        ESP_LOADER_ERROR_UNSUPPORTED_FUNC,
        ESP_LOADER_ERROR_INVALID_RESPONSE,
    } esp_loader_error_t;

    /* ---- chip identifiers ------------------------------------------- */
    typedef enum {
        ESP8266_CHIP  = 0,
        ESP32_CHIP,
        ESP32S2_CHIP,
        ESP32C3_CHIP,
        ESP32S3_CHIP,
        ESP32C2_CHIP,
        ESP32C5_CHIP,
        ESP32H2_CHIP,
        ESP32C6_CHIP,
        ESP32P4_CHIP,
        ESP32C61_CHIP,
        ESP_MAX_CHIP,
        ESP_UNKNOWN_CHIP,
    } target_chip_t;

    /* ---- connection args -------------------------------------------- */
    typedef struct {
        uint32_t sync_timeout;
        int32_t  trials;
    } esp_loader_connect_args_t;

    /* ---- flash config ----------------------------------------------- */
    typedef struct {
        uint32_t offset;
        uint32_t image_size;
        uint32_t block_size;
        _Bool    skip_verify;
        ...;
    } esp_loader_flash_cfg_t;

    typedef struct {
        uint32_t offset;
        uint32_t image_size;
        uint32_t compressed_size;
        uint32_t block_size;
        ...;
    } esp_loader_flash_deflate_cfg_t;

    /* ---- opaque internal contexts ------------------------------------ */
    typedef struct { ...; } esp_loader_t;
    typedef struct { ...; } esf_port_t;

    /* ---- helper: initialise port + loader in one call ---------------- */
    esp_loader_error_t _esf_open(esf_port_t   *port,
                                  esp_loader_t *loader,
                                  const char   *device,
                                  unsigned      baudrate);

    esp_loader_error_t _esf_flash_id(esp_loader_t *loader, uint32_t *flash_id);

    /* ---- connection -------------------------------------------------- */
    esp_loader_error_t esp_loader_connect(
        esp_loader_t *loader, esp_loader_connect_args_t *args);

    esp_loader_error_t esp_loader_connect_with_stub(
        esp_loader_t *loader, esp_loader_connect_args_t *args);

    void esp_loader_deinit(esp_loader_t *loader);

    target_chip_t esp_loader_get_target(esp_loader_t *loader);

    /* ---- flash write ------------------------------------------------- */
    esp_loader_error_t esp_loader_flash_start(
        esp_loader_t *loader, esp_loader_flash_cfg_t *cfg);

    esp_loader_error_t esp_loader_flash_write(
        esp_loader_t *loader, esp_loader_flash_cfg_t *cfg,
        void *payload, uint32_t size);

    esp_loader_error_t esp_loader_flash_finish(
        esp_loader_t *loader, esp_loader_flash_cfg_t *cfg);

    /* ---- compressed flash write -------------------------------------- */
    esp_loader_error_t esp_loader_flash_deflate_start(
        esp_loader_t *loader, esp_loader_flash_deflate_cfg_t *cfg);

    esp_loader_error_t esp_loader_flash_deflate_write(
        esp_loader_t *loader, esp_loader_flash_deflate_cfg_t *cfg,
        void *payload, uint32_t size);

    esp_loader_error_t esp_loader_flash_deflate_finish(
        esp_loader_t *loader, esp_loader_flash_deflate_cfg_t *cfg);

    /* ---- erase ------------------------------------------------------- */
    esp_loader_error_t esp_loader_flash_erase(esp_loader_t *loader);

    esp_loader_error_t esp_loader_flash_erase_region(
        esp_loader_t *loader, uint32_t offset, uint32_t size);

    /* ---- flash read -------------------------------------------------- */
    esp_loader_error_t esp_loader_flash_read(
        esp_loader_t *loader, uint8_t *buf,
        uint32_t offset, uint32_t size);

    esp_loader_error_t esp_loader_flash_detect_size(
        esp_loader_t *loader, uint32_t *flash_size);

    esp_loader_error_t esp_loader_flash_verify_known_md5(
        esp_loader_t *loader, uint32_t address,
        uint32_t size, const uint8_t *expected_md5);

    /* ---- baud rate --------------------------------------------------- */
    esp_loader_error_t esp_loader_change_transmission_rate(
        esp_loader_t *loader, uint32_t rate);

    esp_loader_error_t esp_loader_change_transmission_rate_stub(
        esp_loader_t *loader, uint32_t old_rate, uint32_t new_rate);

    /* ---- register access --------------------------------------------- */
    esp_loader_error_t esp_loader_read_register(
        esp_loader_t *loader, uint32_t addr, uint32_t *value);

    esp_loader_error_t esp_loader_write_register(
        esp_loader_t *loader, uint32_t addr, uint32_t value);

    /* ---- misc -------------------------------------------------------- */
    esp_loader_error_t esp_loader_read_mac(
        esp_loader_t *loader, uint8_t mac[6]);

    void esp_loader_reset_target(esp_loader_t *loader);
""")

# --- Resolve paths relative to this file's repo root -------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
_esf = os.path.join(_root, "esp-serial-flasher")
_port = os.path.join(_root, "port")


def _esf_src(*parts):
    return os.path.join(_esf, *parts)


def _port_src(*parts):
    return os.path.join(_port, *parts)


_platform_serial = (
    _port_src("platform", "win", "serial.c")
    if sys.platform == "win32"
    else _port_src("platform", "posix", "serial.c")
)

_sources = [
    # ESF core
    _esf_src("src", "esp_loader.c"),
    _esf_src("src", "protocol_serial.c"),
    _esf_src("src", "protocol_uart.c"),
    _esf_src("src", "protocol_spi.c"),
    _esf_src("src", "protocol_sdio.c"),
    _esf_src("src", "slip.c"),
    _esf_src("src", "esp_targets.c"),
    _esf_src("src", "md5_hash.c"),
    _esf_src("src", "esp_sdio_stubs.c"),
    # Stub binaries (one C file per chip, plus the dispatch table)
    _esf_src("src", "stubs", "esp_stub_esp8266.c"),
    _esf_src("src", "stubs", "esp_stub_esp32.c"),
    _esf_src("src", "stubs", "esp_stub_esp32s2.c"),
    _esf_src("src", "stubs", "esp_stub_esp32c3.c"),
    _esf_src("src", "stubs", "esp_stub_esp32s3.c"),
    _esf_src("src", "stubs", "esp_stub_esp32c2.c"),
    _esf_src("src", "stubs", "esp_stub_esp32c5.c"),
    _esf_src("src", "stubs", "esp_stub_esp32h2.c"),
    _esf_src("src", "stubs", "esp_stub_esp32c6.c"),
    _esf_src("src", "stubs", "esp_stub_esp32p4.c"),
    _esf_src("src", "stubs", "esp_stub_esp32p4rev1.c"),
    _esf_src("src", "stubs", "esp_stub_esp32c61.c"),
    _esf_src("src", "stubs", "esp_stubs_table.c"),
    # Port layer
    _port_src("esf_port.c"),
    _platform_serial,
]

_include_dirs = [
    _esf_src("include"),
    _esf_src("private_include"),
    _port,
]

_extra_compile_args = ["-DSERIAL_FLASHER_WRITE_BLOCK_RETRIES=3"]
if sys.platform != "win32":
    _extra_compile_args.extend(
        ["-D_POSIX_C_SOURCE=200809L", "-D_DEFAULT_SOURCE", "-std=c11"]
    )
_libraries = ["advapi32"] if sys.platform == "win32" else []

ffi.set_source(
    "esptool._esf",
    """
    #include <stdint.h>
    #include <stdbool.h>
    #include "esp_loader.h"
    #include "esp_loader_error.h"
    #include "esp_targets.h"
    #include "esf_port.h"

    /*
     * Convenience init: wire the port vtable, device path, and baud rate,
     * then call esp_loader_init_uart().  Keeps Python from having to
     * navigate nested struct pointers.
     */
    esp_loader_error_t _esf_open(esf_port_t   *port,
                                  esp_loader_t *loader,
                                  const char   *device,
                                  unsigned      baudrate)
    {
        port->port.ops = &esf_port_ops;
        port->device   = device;
        port->baudrate = baudrate;
        return esp_loader_init_uart(loader, &port->port);
    }

    static esp_loader_error_t _esf_spi_set_lengths(esp_loader_t *loader,
                                                   uint32_t mosi_bits,
                                                   uint32_t miso_bits)
    {
        if (loader->_target == ESP8266_CHIP) {
            uint32_t mosi_mask = (mosi_bits == 0) ? 0 : mosi_bits - 1;
            uint32_t miso_mask = (miso_bits == 0) ? 0 : miso_bits - 1;
            return esp_loader_write_register(
                loader, loader->_reg->usr1, (miso_mask << 8) | (mosi_mask << 17));
        }

        if (mosi_bits > 0) {
            esp_loader_error_t err = esp_loader_write_register(
                loader, loader->_reg->mosi_dlen, mosi_bits - 1);
            if (err != ESP_LOADER_SUCCESS) {
                return err;
            }
        }
        if (miso_bits > 0) {
            esp_loader_error_t err = esp_loader_write_register(
                loader, loader->_reg->miso_dlen, miso_bits - 1);
            if (err != ESP_LOADER_SUCCESS) {
                return err;
            }
        }
        return ESP_LOADER_SUCCESS;
    }

    esp_loader_error_t _esf_flash_id(esp_loader_t *loader, uint32_t *flash_id)
    {
        uint32_t flash_size;
        esp_loader_error_t err = esp_loader_flash_detect_size(loader, &flash_size);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }

        const uint32_t SPI_USR_CMD = (1u << 31);
        const uint32_t SPI_USR_MISO = (1u << 28);
        const uint32_t SPI_CMD_USR = (1u << 18);
        const uint32_t CMD_LEN_SHIFT = 28;
        const uint32_t SPI_FLASH_READ_ID = 0x9F;

        uint32_t old_spi_usr;
        uint32_t old_spi_usr2;
        err = esp_loader_read_register(loader, loader->_reg->usr, &old_spi_usr);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }
        err = esp_loader_read_register(loader, loader->_reg->usr2, &old_spi_usr2);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }

        err = _esf_spi_set_lengths(loader, 0, 24);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }
        err = esp_loader_write_register(
            loader, loader->_reg->usr, SPI_USR_CMD | SPI_USR_MISO);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }
        err = esp_loader_write_register(
            loader, loader->_reg->usr2, (7u << CMD_LEN_SHIFT) | SPI_FLASH_READ_ID);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }
        err = esp_loader_write_register(loader, loader->_reg->w0, 0);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }
        err = esp_loader_write_register(loader, loader->_reg->cmd, SPI_CMD_USR);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }

        for (uint32_t trials = 10; trials > 0; --trials) {
            uint32_t cmd_reg;
            err = esp_loader_read_register(loader, loader->_reg->cmd, &cmd_reg);
            if (err != ESP_LOADER_SUCCESS) {
                return err;
            }
            if ((cmd_reg & SPI_CMD_USR) == 0) {
                break;
            }
            if (trials == 1) {
                return ESP_LOADER_ERROR_TIMEOUT;
            }
        }

        err = esp_loader_read_register(loader, loader->_reg->w0, flash_id);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }
        err = esp_loader_write_register(loader, loader->_reg->usr, old_spi_usr);
        if (err != ESP_LOADER_SUCCESS) {
            return err;
        }
        return esp_loader_write_register(loader, loader->_reg->usr2, old_spi_usr2);
    }
    """,
    sources=_sources,
    include_dirs=_include_dirs,
    extra_compile_args=_extra_compile_args,
    libraries=_libraries,
)

if __name__ == "__main__":
    ffi.compile(verbose=True)
