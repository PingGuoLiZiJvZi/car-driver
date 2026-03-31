/**
 * @file chassis.h
 * @brief Mecanum wheel chassis control library for Orange Pi Zero 3.
 *
 * Controls 4 TT motors via two mini L298N modules (no EN pin) using 8 GPIO
 * pins through the wiringOP library. Provides omnidirectional movement with
 * inverse kinematics and proportional speed clamping.
 */

#ifndef CHASSIS_H
#define CHASSIS_H

#ifdef __cplusplus
extern "C" {
#endif

/* ────────────────────────── Log Levels ────────────────────────── */

#define CHASSIS_LOG_DEBUG 0
#define CHASSIS_LOG_INFO  1
#define CHASSIS_LOG_ERROR 2

/* ────────────────────────── Pin Index Map ─────────────────────── */

/**
 * The 8-element pin array passed to chassis_init() must follow this layout:
 *   [0] FL_IN1   [1] FL_IN2   (Front-Left  motor)
 *   [2] FR_IN1   [3] FR_IN2   (Front-Right motor)
 *   [4] RL_IN1   [5] RL_IN2   (Rear-Left   motor)
 *   [6] RR_IN1   [7] RR_IN2   (Rear-Right  motor)
 */
#define FL_IN1 0
#define FL_IN2 1
#define FR_IN1 2
#define FR_IN2 3
#define RL_IN1 4
#define RL_IN2 5
#define RR_IN1 6
#define RR_IN2 7

#define CHASSIS_PIN_COUNT 8

/* ──────────────────────── Public API ──────────────────────────── */

/**
 * @brief Set the global log level (default: CHASSIS_LOG_INFO).
 */
void chassis_set_log_level(int level);

/**
 * @brief Initialize the chassis with 8 GPIO pin numbers.
 *
 * Calls wiringPiSetup(), sets all pins to OUTPUT mode, and creates
 * soft-PWM threads on every pin (range 0-100). Must be called before
 * any movement function.
 *
 * @param pins  Array of 8 GPIO pin numbers (wiringPi numbering).
 * @return  0 on success, -1 on failure.
 */
int chassis_init(const int pins[8]);

/**
 * @brief Set omnidirectional velocity using normalized inputs.
 *
 * Uses simplified mecanum inverse kinematics with proportional clamping.
 *
 * @param vx     X-axis translation, right(+) / left(-),  range [-1.0, 1.0].
 * @param vy     Y-axis translation, fwd(+)  / back(-),   range [-1.0, 1.0].
 * @param omega  Z-axis rotation,    CW(+)   / CCW(-),    range [-1.0, 1.0].
 * @return  0 on success, -1 if not initialized.
 */
int chassis_set_velocity(float vx, float vy, float omega);

/**
 * @brief Stop all motors and reset all pins to LOW.
 *
 * Should be called before program exit to prevent runaway motors.
 */
void chassis_cleanup(void);

#ifdef __cplusplus
}
#endif

#endif /* CHASSIS_H */
