"""
Optional ESF (esp-serial-flasher) backend for esptool.

Activated by setting the environment variable ESPTOOL_ESF=1.
The C extension must be built first:
    python esptool/_esf_build.py

This module exposes ESFLoader, a drop-in replacement for ESPLoader at the
serial protocol level.  It calls into esp-serial-flasher via cffi — no
pyserial, no Python-level SLIP framing, no stub upload logic.
"""

import importlib

from .logger import log
from .util import FatalError, NotSupportedError

# ---------------------------------------------------------------------------
# Lazy-load the compiled cffi extension.
# ---------------------------------------------------------------------------

try:
    _ext = importlib.import_module("esptool._esf")

    _ffi = _ext.ffi
    _lib = _ext.lib
    _ESF_AVAILABLE = True
except ImportError:
    _ESF_AVAILABLE = False

# ---------------------------------------------------------------------------
# Chip name table (mirrors ESF's target_chip_t enum order).
# ---------------------------------------------------------------------------

_CHIP_NAMES = {
    0: "ESP8266",
    1: "ESP32",
    2: "ESP32-S2",
    3: "ESP32-C3",
    4: "ESP32-S3",
    5: "ESP32-C2",
    6: "ESP32-C5",
    7: "ESP32-H2",
    8: "ESP32-C6",
    9: "ESP32-P4",
    10: "ESP32-C61",
}

# Default connection parameters (mirrors ESP_LOADER_CONNECT_DEFAULT).
_CONNECT_SYNC_TIMEOUT_MS = 100
_CONNECT_TRIALS = 7


class _PortCompat:
    def __init__(self, port):
        self.port = port

    def close(self):
        pass

    def open(self):
        pass


def _check(err, msg="ESF error"):
    if err != _lib.ESP_LOADER_SUCCESS:
        raise FatalError(f"{msg}: error code {int(err)}")


# ---------------------------------------------------------------------------


class ESFLoader:
    """
    ESP chip loader backed by esp-serial-flasher (C) via cffi.

    Implements the subset of ESPLoader's interface used by cmds.py so that
    the two backends are interchangeable.  IS_STUB = True because ESF always
    connects with the flasher stub.
    """

    IS_STUB = True
    FLASH_WRITE_SIZE = 0x4000  # stub default; matches StubMixin
    FLASH_SECTOR_SIZE = 0x1000
    FLASH_ENCRYPTED_WRITE_ALIGN = 16
    WRITE_FLASH_ATTEMPTS = 3
    BOOTLOADER_FLASH_OFFSET = 0x1000
    ESP_IMAGE_MAGIC = 0xE9

    def __init__(self, port, baud=115200):
        if not _ESF_AVAILABLE:
            raise FatalError(
                "ESF backend is not available — build it with:\n"
                "    python esptool/_esf_build.py"
            )

        self._port_str = port if isinstance(port, str) else port.decode()
        self._baud = baud
        self._port = _PortCompat(self._port_str)
        self.secure_download_mode = False
        self.stub_is_disabled = False

        # Allocate C structs; cffi computes sizes from the compiled headers.
        self._port_cdata = _ffi.new("esf_port_t *")
        self._loader_cdata = _ffi.new("esp_loader_t *")

        # Keep the device string alive as long as the loader is open.
        self._device_cstr = _ffi.new("char[]", self._port_str.encode() + b"\x00")
        self._flash_cfg = None
        self._flash_defl_cfg = None

        _check(
            _lib._esf_open(
                self._port_cdata,
                self._loader_cdata,
                self._device_cstr,
                baud,
            ),
            f"Failed to open {self._port_str}",
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._loader_cdata is not None:
            _lib.esp_loader_deinit(self._loader_cdata)
            self._loader_cdata = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, mode="default-reset", attempts=None, **_kwargs):
        if mode in ("no-reset", "no-reset-no-sync"):
            log.note(f'Pre-connection mode "{mode}" selected.')

        args = _ffi.new("esp_loader_connect_args_t *")
        args.sync_timeout = _CONNECT_SYNC_TIMEOUT_MS
        args.trials = attempts if attempts is not None else _CONNECT_TRIALS

        log.print("Connecting (ESF)...", end="", flush=True)
        err = _lib.esp_loader_connect_with_stub(self._loader_cdata, args)
        log.print("")
        _check(err, f"Failed to connect to chip on {self._port_str}")

    @property
    def CHIP_NAME(self):
        chip = int(_lib.esp_loader_get_target(self._loader_cdata))
        return _CHIP_NAMES.get(chip, f"Unknown({chip})")

    @property
    def serial_port(self):
        return self._port_str

    def get_chip_description(self):
        return self.CHIP_NAME

    def get_chip_features(self):
        return []

    def get_crystal_freq(self):
        return 40

    def get_usb_mode(self):
        return None

    def get_usb_vid_pid(self):
        return (None, None)

    def get_secure_boot_enabled(self):
        return False

    def get_secure_boot_v1_enabled(self):
        return False

    def get_flash_encryption_enabled(self):
        return False

    def get_encrypted_download_disabled(self):
        return False

    def get_flash_crypt_config(self):
        return None

    def is_flash_encryption_key_valid(self):
        return True

    def uses_key_manager_for_flash_encryption(self):
        return False

    def read_spiflash_sfdp(self, addr, size):
        return 0

    def run_spiflash_command(self, *args, **kwargs):
        return 0

    def flash_type(self):
        return None

    def flash_set_parameters(self, size):
        pass

    def flash_md5sum(self, address, size):
        raise NotSupportedError(self, "flash_md5sum")

    def flash_verify_known_md5(self, address, size, expected_md5):
        expected = expected_md5.encode("ascii")
        buf = _ffi.from_buffer(expected)
        _check(
            _lib.esp_loader_flash_verify_known_md5(
                self._loader_cdata, address, size, buf
            ),
            "Flash MD5 verification failed",
        )

    def get_flash_voltage(self):
        raise NotSupportedError(self, "get_flash_voltage")

    def chip_id(self):
        raise NotSupportedError(self, "chip_id")

    def run_stub(self, stub=None):
        return self

    # ------------------------------------------------------------------
    # Flash write
    # ------------------------------------------------------------------

    def flash_begin(self, size, offset, encrypted_write=False, logging=True):
        self._flash_cfg = _ffi.new("esp_loader_flash_cfg_t *")
        self._flash_cfg.offset = offset
        self._flash_cfg.image_size = size
        self._flash_cfg.block_size = self.FLASH_WRITE_SIZE
        self._flash_cfg.skip_verify = True
        _check(
            _lib.esp_loader_flash_start(self._loader_cdata, self._flash_cfg),
            "Failed to begin flash write",
        )
        return (size + self.FLASH_WRITE_SIZE - 1) // self.FLASH_WRITE_SIZE

    def flash_block(self, data, seq, timeout=None, encrypted=False):
        buf = _ffi.from_buffer(data)
        _check(
            _lib.esp_loader_flash_write(
                self._loader_cdata, self._flash_cfg, buf, len(data)
            ),
            f"Failed to write flash block {seq}",
        )

    def flash_finish(self, reboot=False, timeout=None):
        _check(
            _lib.esp_loader_flash_finish(self._loader_cdata, self._flash_cfg),
            "Failed to finish flash write",
        )
        self._flash_cfg = None
        if reboot:
            self.hard_reset()

    # ------------------------------------------------------------------
    # Compressed flash write
    # ------------------------------------------------------------------

    def flash_defl_begin(self, size, compsize, offset, encrypted_write=False):
        self._flash_defl_cfg = _ffi.new("esp_loader_flash_deflate_cfg_t *")
        self._flash_defl_cfg.offset = offset
        self._flash_defl_cfg.image_size = size
        self._flash_defl_cfg.compressed_size = compsize
        self._flash_defl_cfg.block_size = self.FLASH_WRITE_SIZE
        _check(
            _lib.esp_loader_flash_deflate_start(
                self._loader_cdata, self._flash_defl_cfg
            ),
            "Failed to begin compressed flash write",
        )
        return (compsize + self.FLASH_WRITE_SIZE - 1) // self.FLASH_WRITE_SIZE

    def flash_defl_block(self, data, seq, timeout=None):
        buf = _ffi.from_buffer(data)
        _check(
            _lib.esp_loader_flash_deflate_write(
                self._loader_cdata, self._flash_defl_cfg, buf, len(data)
            ),
            f"Failed to write compressed flash block {seq}",
        )

    def flash_defl_finish(self, reboot=False, timeout=None):
        _check(
            _lib.esp_loader_flash_deflate_finish(
                self._loader_cdata, self._flash_defl_cfg
            ),
            "Failed to finish compressed flash write",
        )
        self._flash_defl_cfg = None
        if reboot:
            self.hard_reset()

    # ------------------------------------------------------------------
    # Erase
    # ------------------------------------------------------------------

    def erase_flash(self):
        _check(
            _lib.esp_loader_flash_erase(self._loader_cdata),
            "Failed to erase flash",
        )

    def erase_region(self, offset, size):
        _check(
            _lib.esp_loader_flash_erase_region(self._loader_cdata, offset, size),
            f"Failed to erase region {offset:#x}+{size:#x}",
        )

    # ------------------------------------------------------------------
    # Flash read
    # ------------------------------------------------------------------

    def read_flash(self, offset, length, progress_fn=None):
        buf = _ffi.new("uint8_t[]", length)
        _check(
            _lib.esp_loader_flash_read(self._loader_cdata, buf, offset, length),
            f"Failed to read flash at {offset:#x}",
        )
        data = bytes(_ffi.buffer(buf, length))
        if progress_fn:
            progress_fn(length, length, offset)
        return data

    def flash_id(self, cache=True):
        flash_id_p = _ffi.new("uint32_t *")
        _check(
            _lib._esf_flash_id(self._loader_cdata, flash_id_p),
            "Failed to read flash ID",
        )
        return int(flash_id_p[0])

    # ------------------------------------------------------------------
    # Baud rate
    # ------------------------------------------------------------------

    def change_baud(self, baud):
        log.print(f"Changing baud rate to {baud}...")
        _check(
            _lib.esp_loader_change_transmission_rate_stub(
                self._loader_cdata, self._baud, baud
            ),
            f"Failed to change baud rate to {baud}",
        )
        self._baud = baud
        log.print("Changed.")

    # ------------------------------------------------------------------
    # Register access
    # ------------------------------------------------------------------

    def read_reg(self, addr):
        val = _ffi.new("uint32_t *")
        _check(
            _lib.esp_loader_read_register(self._loader_cdata, addr, val),
            f"Failed to read register {addr:#010x}",
        )
        return int(val[0])

    def write_reg(self, addr, value, mask=0xFFFFFFFF, delay_us=0, delay_after_us=0):
        # ESF write_register has no mask/delay support; apply mask in Python.
        if mask != 0xFFFFFFFF:
            current = self.read_reg(addr)
            value = (current & ~mask) | (value & mask)
        _check(
            _lib.esp_loader_write_register(self._loader_cdata, addr, value),
            f"Failed to write register {addr:#010x}",
        )

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def hard_reset(self, uses_usb=False):
        log.print("Hard resetting via RTS pin...")
        _lib.esp_loader_reset_target(self._loader_cdata)

    # ------------------------------------------------------------------
    # MAC address
    # ------------------------------------------------------------------

    def read_mac(self, mac_type="BASE_MAC"):
        mac_buf = _ffi.new("uint8_t[6]")
        _check(
            _lib.esp_loader_read_mac(self._loader_cdata, mac_buf),
            "Failed to read MAC address",
        )
        return tuple(mac_buf[i] for i in range(6))
