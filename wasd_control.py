#!/usr/bin/env python3
"""
WASD long-press controller for libchassis.so on OrangePi Zero3.

Controls:
  W/S: forward/backward (hold to keep moving)
  A/D: left/right strafe (hold to keep moving)
  SPACE: stop immediately
  Q/X/ESC/Ctrl-C: quit

Long-press is implemented using terminal key repeat + timeout.
By default, left/right is corrected for the known inversion observed in
simple_control.py on this vehicle setup.
"""

import argparse
import ctypes
import os
import select
import sys
import termios
import time
from pathlib import Path


PIN_ARRAY = (ctypes.c_int * 8)(6, 5, 7, 8, 10, 9, 13, 16)


def clamp_unit(v: float) -> float:
    return max(-1.0, min(1.0, v))


def load_library() -> ctypes.CDLL:
    lib_path = Path(__file__).resolve().parent / "libchassis.so"
    if not lib_path.exists():
        print("ERROR: libchassis.so not found, please run: make")
        sys.exit(1)

    lib = ctypes.CDLL(str(lib_path))
    lib.chassis_set_log_level.argtypes = [ctypes.c_int]
    lib.chassis_set_log_level.restype = None

    lib.chassis_init.argtypes = [ctypes.POINTER(ctypes.c_int)]
    lib.chassis_init.restype = ctypes.c_int

    lib.chassis_set_velocity.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
    lib.chassis_set_velocity.restype = ctypes.c_int

    lib.chassis_cleanup.argtypes = []
    lib.chassis_cleanup.restype = None
    return lib


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WASD long-press controller for libchassis.so")
    parser.add_argument("--speed", type=float, default=0.5, help="Normalized speed in [0.0, 1.0]")
    parser.add_argument("--hz", type=float, default=30.0, help="Control loop frequency")
    parser.add_argument(
        "--hold-ms",
        type=float,
        default=180.0,
        help="Key hold timeout in milliseconds (long-press detection window)",
    )
    parser.add_argument(
        "--log-level",
        type=int,
        default=1,
        choices=[0, 1, 2],
        help="0=DEBUG, 1=INFO, 2=ERROR",
    )
    parser.add_argument(
        "--no-lr-fix",
        action="store_true",
        help="Disable left/right correction and use raw A-left, D-right mapping",
    )
    return parser.parse_args()


def set_noncanonical_noecho(fd: int) -> list:
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[3] &= ~(termios.ICANON | termios.ECHO)
    new[6][termios.VMIN] = 0
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, new)
    return old


def read_pending_keys(fd: int) -> list[str]:
    keys: list[str] = []
    while True:
        readable, _, _ = select.select([fd], [], [], 0.0)
        if not readable:
            break

        chunk = os.read(fd, 64)
        if not chunk:
            break

        keys.extend(chunk.decode(errors="ignore"))
    return keys


def main() -> int:
    args = parse_args()
    speed = max(0.0, min(1.0, args.speed))
    hz = max(1.0, args.hz)
    hold_window = max(0.01, args.hold_ms / 1000.0)
    period = 1.0 / hz
    lr_fix_enabled = not args.no_lr_fix

    if not sys.stdin.isatty():
        print("ERROR: stdin is not a TTY. Run this script in an interactive terminal.")
        return 1

    # wiringPi typically needs root or /dev/gpiomem access on target boards.
    if os.geteuid() != 0 and not os.access("/dev/gpiomem", os.R_OK | os.W_OK):
        print("ERROR: GPIO access denied. Please run with sudo.")
        return 1

    lib = load_library()
    lib.chassis_set_log_level(args.log_level)

    if lib.chassis_init(PIN_ARRAY) != 0:
        print("ERROR: chassis_init failed")
        return 1

    print("Using wiringPi pins: [6, 5, 7, 8, 10, 9, 13, 16]")
    print("Pin order: [FL_IN1, FL_IN2, FR_IN1, FR_IN2, RL_IN1, RL_IN2, RR_IN1, RR_IN2]")
    print("\nWASD long-press mode started.")
    print("Hold keys to move: W/S forward/backward, A/D left/right")
    print("SPACE=stop, Q/X/ESC/Ctrl-C=quit")
    print(f"Left/right correction: {'ON' if lr_fix_enabled else 'OFF'}")

    fd = sys.stdin.fileno()
    old_term = set_noncanonical_noecho(fd)

    key_last_seen = {"w": -1e9, "a": -1e9, "s": -1e9, "d": -1e9}
    last_cmd: tuple[float, float, float] | None = None

    if lr_fix_enabled:
        # Compensate for the observed left/right inversion from simple_control.py.
        a_sign = 1.0
        d_sign = -1.0
    else:
        a_sign = -1.0
        d_sign = 1.0

    try:
        running = True
        while running:
            now = time.monotonic()
            for ch in read_pending_keys(fd):
                c = ch.lower()
                if c in key_last_seen:
                    key_last_seen[c] = now
                elif c == " ":
                    for k in key_last_seen:
                        key_last_seen[k] = -1e9
                elif c in {"q", "x", "\x1b", "\x03"}:
                    running = False

            now = time.monotonic()
            w_on = (now - key_last_seen["w"]) <= hold_window
            s_on = (now - key_last_seen["s"]) <= hold_window
            a_on = (now - key_last_seen["a"]) <= hold_window
            d_on = (now - key_last_seen["d"]) <= hold_window

            vx = 0.0
            vy = 0.0

            if w_on:
                vy += speed
            if s_on:
                vy -= speed
            if a_on:
                vx += a_sign * speed
            if d_on:
                vx += d_sign * speed

            cmd = (clamp_unit(vx), clamp_unit(vy), 0.0)
            if cmd != last_cmd:
                if lib.chassis_set_velocity(cmd[0], cmd[1], cmd[2]) != 0:
                    print("\nERROR: chassis_set_velocity failed")
                    return 1
                print(f"\rCommand vx={cmd[0]:+0.2f} vy={cmd[1]:+0.2f}     ", end="", flush=True)
                last_cmd = cmd

            time.sleep(period)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old_term)
        lib.chassis_set_velocity(0.0, 0.0, 0.0)
        lib.chassis_cleanup()
        print("\nStopped and cleaned up.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
