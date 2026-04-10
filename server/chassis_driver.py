from __future__ import annotations

import ctypes
import logging
import os
import threading
from pathlib import Path

LOGGER = logging.getLogger(__name__)

# Pin order: [FL_IN1, FL_IN2, FR_IN1, FR_IN2, RL_IN1, RL_IN2, RR_IN1, RR_IN2]
DEFAULT_PINS = (6, 5, 7, 8, 10, 9, 13, 16)


class ChassisError(RuntimeError):
    pass


class ChassisDriver:
    def __init__(self, lib_path: Path, pins: tuple[int, ...] = DEFAULT_PINS, dry_run: bool = False) -> None:
        if len(pins) != 8:
            raise ValueError("pins must contain exactly 8 values")

        self.lib_path = lib_path
        self.pins = pins
        self.dry_run = dry_run

        self._lib: ctypes.CDLL | None = None
        self._pin_array = (ctypes.c_int * 8)(*pins)
        self._lock = threading.Lock()
        self._initialized = False

    def open(self, log_level: int = 1) -> None:
        with self._lock:
            if self._initialized:
                return

            if self.dry_run:
                LOGGER.warning("CHASSIS_DRY_RUN enabled, motor commands will only be logged")
                self._initialized = True
                return

            if not self.lib_path.exists():
                raise ChassisError(f"libchassis.so not found: {self.lib_path}")

            self._lib = ctypes.CDLL(str(self.lib_path))
            self._bind_abi(self._lib)
            self._lib.chassis_set_log_level(log_level)

            rc = self._lib.chassis_init(self._pin_array)
            if rc != 0:
                raise ChassisError("chassis_init failed")

            self._initialized = True
            LOGGER.info("Chassis initialized with pins: %s", list(self.pins))

    def set_velocity(self, vx: float, vy: float, omega: float = 0.0) -> None:
        vx = _clamp_unit(vx)
        vy = _clamp_unit(vy)
        omega = _clamp_unit(omega)

        with self._lock:
            if not self._initialized:
                raise ChassisError("chassis is not initialized")

            if self.dry_run:
                LOGGER.debug("[DRY-RUN] set_velocity vx=%.3f vy=%.3f omega=%.3f", vx, vy, omega)
                return

            assert self._lib is not None
            rc = self._lib.chassis_set_velocity(vx, vy, omega)
            if rc != 0:
                raise ChassisError("chassis_set_velocity failed")

    def stop(self) -> None:
        self.set_velocity(0.0, 0.0, 0.0)

    def close(self) -> None:
        with self._lock:
            if not self._initialized:
                return

            if self.dry_run:
                self._initialized = False
                return

            assert self._lib is not None
            try:
                self._lib.chassis_set_velocity(0.0, 0.0, 0.0)
            except Exception:
                LOGGER.exception("Failed to send final stop command")

            try:
                self._lib.chassis_cleanup()
            except Exception:
                LOGGER.exception("chassis_cleanup failed")

            self._initialized = False
            LOGGER.info("Chassis cleaned up")

    @staticmethod
    def from_env(project_root: Path) -> "ChassisDriver":
        lib_path = Path(os.environ.get("CHASSIS_LIB", project_root / "gpio" / "libchassis.so"))
        dry_run = os.environ.get("CHASSIS_DRY_RUN", "0") == "1"
        return ChassisDriver(lib_path=lib_path, dry_run=dry_run)

    @staticmethod
    def _bind_abi(lib: ctypes.CDLL) -> None:
        lib.chassis_set_log_level.argtypes = [ctypes.c_int]
        lib.chassis_set_log_level.restype = None

        lib.chassis_init.argtypes = [ctypes.POINTER(ctypes.c_int)]
        lib.chassis_init.restype = ctypes.c_int

        lib.chassis_set_velocity.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
        lib.chassis_set_velocity.restype = ctypes.c_int

        lib.chassis_cleanup.argtypes = []
        lib.chassis_cleanup.restype = None


def _clamp_unit(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))
