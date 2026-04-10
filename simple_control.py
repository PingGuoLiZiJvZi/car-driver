#!/usr/bin/env python3
"""
Simple Python controller for libchassis.so on OrangePi Zero3.

Selected 8 available wiringPi pins (all currently free, from physical pins 1-26):
    FL_IN1=6   (physical 12, PC11)
    FL_IN2=5   (physical 11, PC6)
  FR_IN1=7   (physical 13, PC5)
  FR_IN2=8   (physical 15, PC8)
    RL_IN1=10  (physical 18, PC14)
    RL_IN2=9   (physical 16, PC15)
  RR_IN1=13  (physical 22, PC7)
  RR_IN2=16  (physical 26, PC10)
"""

import argparse
import ctypes
import os
import sys
import time
from pathlib import Path


PIN_ARRAY = (ctypes.c_int * 8)(6, 5, 7, 8, 10, 9, 13, 16)


def clamp_unit(v: float) -> float:
    return max(-1.0, min(1.0, v))


def load_library() -> ctypes.CDLL:
    lib_path = Path(__file__).resolve().parent / "gpio/libchassis.so"
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


def run_once(lib: ctypes.CDLL, vx: float, vy: float, omega: float, duration: float) -> None:
    vx = clamp_unit(vx)
    vy = clamp_unit(vy)
    omega = clamp_unit(omega)

    if lib.chassis_set_velocity(vx, vy, omega) != 0:
        print("ERROR: chassis_set_velocity failed")
        sys.exit(1)

    if duration > 0:
        time.sleep(duration)

    # Always send an explicit stop after movement.
    lib.chassis_set_velocity(0.0, 0.0, 0.0)


def run_demo(lib: ctypes.CDLL, speed: float, duration: float) -> None:
    seq = [
        ("forward", 0.0, speed, 0.0),
        ("backward", 0.0, -speed, 0.0),
        ("left", -speed, 0.0, 0.0),
        ("right", speed, 0.0, 0.0),
        ("rotate_cw", 0.0, 0.0, speed),
        ("rotate_ccw", 0.0, 0.0, -speed),
    ]

    for name, vx, vy, omega in seq:
        print(f"[DEMO] {name}: vx={vx:.2f}, vy={vy:.2f}, omega={omega:.2f}")
        run_once(lib, vx, vy, omega, duration)
        time.sleep(0.2)


def ask_float(prompt: str, default: float, min_value: float | None = None, max_value: float | None = None) -> float:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if raw == "":
            value = default
        else:
            try:
                value = float(raw)
            except ValueError:
                print("Invalid number, please try again.")
                continue

        if min_value is not None and value < min_value:
            print(f"Value must be >= {min_value}")
            continue
        if max_value is not None and value > max_value:
            print(f"Value must be <= {max_value}")
            continue
        return value


def run_interactive(lib: ctypes.CDLL, default_speed: float) -> None:
    action_map = {
        "1": ("forward", 0.0, 1.0, 0.0),
        "2": ("backward", 0.0, -1.0, 0.0),
        "3": ("left", -1.0, 0.0, 0.0),
        "4": ("right", 1.0, 0.0, 0.0),
        "5": ("rotate_cw", 0.0, 0.0, 1.0),
        "6": ("rotate_ccw", 0.0, 0.0, -1.0),
    }

    print("Interactive mode started.")
    print("Input a menu number. Use 0/q/quit/exit to leave.")

    while True:
        print("\n=== Menu ===")
        print("1) forward")
        print("2) backward")
        print("3) left")
        print("4) right")
        print("5) rotate clockwise")
        print("6) rotate counter-clockwise")
        print("7) stop")
        print("8) custom (vx/vy/omega)")
        print("0) quit")

        choice = input("Select action: ").strip().lower()
        if choice in {"0", "q", "quit", "exit"}:
            lib.chassis_set_velocity(0.0, 0.0, 0.0)
            print("Exiting interactive mode.")
            return

        if choice == "7":
            lib.chassis_set_velocity(0.0, 0.0, 0.0)
            print("Stopped.")
            continue

        if choice == "8":
            vx = ask_float("vx (-1.0~1.0)", 0.0, -1.0, 1.0)
            vy = ask_float("vy (-1.0~1.0)", 0.0, -1.0, 1.0)
            omega = ask_float("omega (-1.0~1.0)", 0.0, -1.0, 1.0)
            duration = ask_float("duration seconds", 1.0, 0.0, None)
            print(f"Running custom: vx={vx:.2f}, vy={vy:.2f}, omega={omega:.2f}, t={duration:.2f}s")
            run_once(lib, vx, vy, omega, duration)
            continue

        if choice not in action_map:
            print("Invalid selection.")
            continue

        speed = ask_float("speed (0.0~1.0)", default_speed, 0.0, 1.0)
        duration = ask_float("duration seconds", 1.0, 0.0, None)

        name, vx_factor, vy_factor, omega_factor = action_map[choice]
        vx = vx_factor * speed
        vy = vy_factor * speed
        omega = omega_factor * speed

        print(f"Running {name}: speed={speed:.2f}, t={duration:.2f}s")
        run_once(lib, vx, vy, omega, duration)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple controller for libchassis.so")
    parser.add_argument(
        "action",
        choices=["interactive", "demo", "forward", "backward", "left", "right", "cw", "ccw", "stop"],
        nargs="?",
        default="interactive",
        help="Control action",
    )
    parser.add_argument("--speed", type=float, default=0.5, help="Normalized speed in [0.0, 1.0]")
    parser.add_argument("--duration", type=float, default=1.0, help="Action duration in seconds")
    parser.add_argument(
        "--log-level",
        type=int,
        default=1,
        choices=[0, 1, 2],
        help="0=DEBUG, 1=INFO, 2=ERROR",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    speed = max(0.0, min(1.0, args.speed))
    duration = max(0.0, args.duration)

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

    try:
        if args.action == "interactive":
            run_interactive(lib, speed)
        elif args.action == "demo":
            run_demo(lib, speed, duration)
        elif args.action == "forward":
            run_once(lib, 0.0, speed, 0.0, duration)
        elif args.action == "backward":
            run_once(lib, 0.0, -speed, 0.0, duration)
        elif args.action == "left":
            run_once(lib, -speed, 0.0, 0.0, duration)
        elif args.action == "right":
            run_once(lib, speed, 0.0, 0.0, duration)
        elif args.action == "cw":
            run_once(lib, 0.0, 0.0, speed, duration)
        elif args.action == "ccw":
            run_once(lib, 0.0, 0.0, -speed, duration)
        else:
            lib.chassis_set_velocity(0.0, 0.0, 0.0)
            print("stop")
    finally:
        lib.chassis_cleanup()

    return 0


if __name__ == "__main__":
    sys.exit(main())
