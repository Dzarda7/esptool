"""
Optional ESF (esp-serial-flasher) backend for esptool.

Activated by setting the environment variable ESPTOOL_ESF=1.
The C extension must be built first:
    python esptool/_esf_build.py

This module exposes ESFLoader, a drop-in replacement for ESPLoader at the
serial protocol level.  It calls into esp-serial-flasher via cffi — no
pyserial, no Python-level SLIP framing, no stub upload logic.
"""

from .logger import log
from .util import FatalError

# ---------------------------------------------------------------------------
# Lazy-load the compiled cffi extension.
# ---------------------------------------------------------------------------

try:
    from . import _esf as _ext

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

    def __init__(self, port, baud=115200):
        if not _ESF_AVAILABLE:
            raise FatalError(
                "ESF backend is not available — build it with:\n"
                "    python esptool/_esf_build.py"
            )

        self._port_str = port if isinstance(port, str) else port.decode()
        self._baud = baud

        # Allocate C structs; cffi computes sizes from the compiled headers.
        self._port_cdata = _ffi.new("esf_port_t *")
        self._loader_cdata = _ffi.new("esp_loader_t *")

        # Keep the device string alive as long as the loader is open.
        self._device_cstr = _ffi.new("char[]", self._port_str.encode() + b"\x00")

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

    # ------------------------------------------------------------------
    # Flash write
    # ------------------------------------------------------------------

    def flash_begin(self, size, offset, encrypted_write=False, logging=True):
        cfg = _ffi.new("esp_loader_flash_cfg_t *")
        cfg.offset = offset
        cfg.image_size = size
        cfg.block_size = self.FLASH_WRITE_SIZE
        cfg.skip_verify = False
        _check(
            _lib.esp_loader_flash_start(self._loader_cdata, cfg),
            "Failed to begin flash write",
        )
        return (size + self.FLASH_WRITE_SIZE - 1) // self.FLASH_WRITE_SIZE

    def flash_block(self, data, seq, timeout=None, encrypted=False):
        buf = _ffi.from_buffer(data)
        _check(
            _lib.esp_loader_flash_write(self._loader_cdata, buf, len(data)),
            f"Failed to write flash block {seq}",
        )

    def flash_finish(self, reboot=False, timeout=None):
        _check(
            _lib.esp_loader_flash_finish(self._loader_cdata, reboot),
            "Failed to finish flash write",
        )

    # ------------------------------------------------------------------
    # Compressed flash write
    # ------------------------------------------------------------------

    def flash_defl_begin(self, size, compsize, offset, encrypted_write=False):
        cfg = _ffi.new("esp_loader_flash_deflate_cfg_t *")
        cfg.offset = offset
        cfg.uncompressed_size = size
        cfg.compressed_size = compsize
        cfg.block_size = self.FLASH_WRITE_SIZE
        cfg.skip_verify = False
        _check(
            _lib.esp_loader_flash_deflate_start(self._loader_cdata, cfg),
            "Failed to begin compressed flash write",
        )
        return (compsize + self.FLASH_WRITE_SIZE - 1) // self.FLASH_WRITE_SIZE

    def flash_defl_block(self, data, seq, timeout=None):
        buf = _ffi.from_buffer(data)
        _check(
            _lib.esp_loader_flash_deflate_write(self._loader_cdata, buf, len(data)),
            f"Failed to write compressed flash block {seq}",
        )

    def flash_defl_finish(self, reboot=False, timeout=None):
        _check(
            _lib.esp_loader_flash_deflate_finish(self._loader_cdata, reboot),
            "Failed to finish compressed flash write",
        )

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
            _lib.esp_loader_flash_read(self._loader_cdata, offset, buf, length),
            f"Failed to read flash at {offset:#x}",
        )
        data = bytes(_ffi.buffer(buf, length))
        if progress_fn:
            progress_fn(length, length, offset)
        return data

    def flash_id(self, cache=True):
        size_p = _ffi.new("uint32_t *")
        _check(
            _lib.esp_loader_flash_detect_size(self._loader_cdata, size_p),
            "Failed to detect flash size",
        )
        return int(size_p[0])

    # ------------------------------------------------------------------
    # Baud rate
    # ------------------------------------------------------------------

    def change_baud(self, baud):
        log.print(f"Changing baud rate to {baud}...")
        _check(
            _lib.esp_loader_change_transmission_rate_stub(self._loader_cdata, baud),
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
