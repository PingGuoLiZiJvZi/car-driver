/**
 * @file chassis.c
 * @brief Mecanum wheel chassis control library implementation.
 *
 * Cross-platform: on ARM64 uses real wiringOP; on x86 uses mock stubs
 * that print pin operations to stdout for development/testing.
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <math.h>
#include <string.h>

#include "chassis.h"

/* ══════════════════════════════════════════════════════════════════
 *  Cross-Platform Mock / Real wiringOP Layer
 * ══════════════════════════════════════════════════════════════════ */

#ifdef __aarch64__
/* ── ARM64: link against real wiringOP ── */
#include <wiringPi.h>
#include <softPwm.h>

#else
/* ── x86 / other: mock stubs ── */

#define OUTPUT 1

static int wiringPiSetup(void) {
    printf("[MOCK] wiringPiSetup() called\n");
    return 0;
}

static void pinMode(int pin, int mode) {
    printf("[MOCK] pinMode(pin=%d, mode=%d)\n", pin, mode);
}

static int softPwmCreate(int pin, int initialValue, int pwmRange) {
    printf("[MOCK] softPwmCreate(pin=%d, init=%d, range=%d)\n",
           pin, initialValue, pwmRange);
    return 0;
}

static void softPwmWrite(int pin, int value) {
    printf("[MOCK] softPwmWrite(pin=%d, value=%d)\n", pin, value);
}

#endif /* __aarch64__ */

/* ══════════════════════════════════════════════════════════════════
 *  Logging
 * ══════════════════════════════════════════════════════════════════ */

static int g_log_level = CHASSIS_LOG_INFO;

void chassis_set_log_level(int level) {
    if (level >= CHASSIS_LOG_DEBUG && level <= CHASSIS_LOG_ERROR) {
        g_log_level = level;
    }
}

static void chassis_log(int level, const char *fmt, ...) {
    if (level < g_log_level) return;

    const char *tag;
    switch (level) {
        case CHASSIS_LOG_DEBUG: tag = "DEBUG"; break;
        case CHASSIS_LOG_INFO:  tag = "INFO";  break;
        case CHASSIS_LOG_ERROR: tag = "ERROR"; break;
        default:                tag = "?????"; break;
    }

    printf("[CHASSIS][%s] ", tag);
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
}

/* ══════════════════════════════════════════════════════════════════
 *  Internal State
 * ══════════════════════════════════════════════════════════════════ */

static int  g_pins[CHASSIS_PIN_COUNT] = {0};
static int  g_initialized = 0;

#define PWM_RANGE 100

/* ══════════════════════════════════════════════════════════════════
 *  Initialization & Cleanup
 * ══════════════════════════════════════════════════════════════════ */

int chassis_init(const int pins[8]) {
    if (pins == NULL) {
        chassis_log(CHASSIS_LOG_ERROR, "chassis_init: pins array is NULL");
        return -1;
    }

    if (g_initialized) {
        chassis_log(CHASSIS_LOG_ERROR,
                    "chassis_init: already initialized, call chassis_cleanup() first");
        return -1;
    }

    /* Set up wiringPi (uses wiringPi pin numbering) */
    if (wiringPiSetup() == -1) {
        chassis_log(CHASSIS_LOG_ERROR, "chassis_init: wiringPiSetup() failed");
        return -1;
    }

    /* Copy pin config & initialise every pin as OUTPUT + softPWM */
    memcpy(g_pins, pins, sizeof(int) * CHASSIS_PIN_COUNT);

    for (int i = 0; i < CHASSIS_PIN_COUNT; i++) {
        pinMode(g_pins[i], OUTPUT);
        if (softPwmCreate(g_pins[i], 0, PWM_RANGE) != 0) {
            chassis_log(CHASSIS_LOG_ERROR,
                        "chassis_init: softPwmCreate failed for pin %d (index %d)",
                        g_pins[i], i);
            return -1;
        }
        chassis_log(CHASSIS_LOG_DEBUG,
                    "Pin %d (index %d) initialized with softPWM", g_pins[i], i);
    }

    g_initialized = 1;
    chassis_log(CHASSIS_LOG_INFO, "Chassis initialized successfully");
    return 0;
}

void chassis_cleanup(void) {
    if (!g_initialized) {
        chassis_log(CHASSIS_LOG_ERROR, "chassis_cleanup: not initialized");
        return;
    }

    /* Pull all pins LOW via softPwmWrite */
    for (int i = 0; i < CHASSIS_PIN_COUNT; i++) {
        softPwmWrite(g_pins[i], 0);
    }

    g_initialized = 0;
    chassis_log(CHASSIS_LOG_INFO, "Chassis cleanup complete, all pins set to LOW");
}

/* ══════════════════════════════════════════════════════════════════
 *  Single Motor Control  (internal)
 *
 *  ALL pin control uses softPwmWrite() exclusively — no digitalWrite().
 *  This avoids softPwm thread leaks and pin-state conflicts.
 * ══════════════════════════════════════════════════════════════════ */

/**
 * @brief Drive a single motor.
 *
 * @param in1_pin  wiringPi pin number for IN1.
 * @param in2_pin  wiringPi pin number for IN2.
 * @param speed    Motor speed in [-100, 100].  Positive = forward, negative =
 *                 reverse, 0 = stop.
 */
static void set_motor(int in1_pin, int in2_pin, int speed) {
    if (speed > 0) {
        /* Forward: IN1 = PWM duty, IN2 = 0 */
        softPwmWrite(in1_pin, speed);
        softPwmWrite(in2_pin, 0);
    } else if (speed < 0) {
        /* Reverse: IN1 = 0, IN2 = PWM duty */
        softPwmWrite(in1_pin, 0);
        softPwmWrite(in2_pin, -speed);
    } else {
        /* Stop */
        softPwmWrite(in1_pin, 0);
        softPwmWrite(in2_pin, 0);
    }

    chassis_log(CHASSIS_LOG_DEBUG,
                "set_motor(IN1=%d, IN2=%d, speed=%d)", in1_pin, in2_pin, speed);
}

/* ══════════════════════════════════════════════════════════════════
 *  Inverse Kinematics & Velocity Control
 *
 *  Simplified mecanum model (no wheelbase/track coefficients):
 *
 *    FL = vy + vx + omega
 *    FR = vy - vx - omega
 *    RL = vy - vx + omega
 *    RR = vy + vx - omega
 *
 *  After computing raw values, apply proportional clamping so the
 *  largest |value| maps to PWM_RANGE (100) while preserving the
 *  direction vector.
 * ══════════════════════════════════════════════════════════════════ */

int chassis_set_velocity(float vx, float vy, float omega) {
    if (!g_initialized) {
        chassis_log(CHASSIS_LOG_ERROR,
                    "chassis_set_velocity: not initialized");
        return -1;
    }

    chassis_log(CHASSIS_LOG_INFO,
                "set_velocity: vx=%.2f  vy=%.2f  omega=%.2f", vx, vy, omega);

    /* ── Inverse kinematics ── */
    float raw[4];
    raw[0] = vy + vx + omega;   /* FL */
    raw[1] = vy - vx - omega;   /* FR */
    raw[2] = vy - vx + omega;   /* RL */
    raw[3] = vy + vx - omega;   /* RR */

    /* ── Proportional clamping / normalisation ── */
    float max_abs = 0.0f;
    for (int i = 0; i < 4; i++) {
        float a = fabsf(raw[i]);
        if (a > max_abs) max_abs = a;
    }

    if (max_abs > 1.0f) {
        chassis_log(CHASSIS_LOG_DEBUG,
                    "Clamping: max_abs=%.3f, scaling down", max_abs);
        for (int i = 0; i < 4; i++) {
            raw[i] /= max_abs;
        }
    }

    /* ── Map [-1.0, 1.0] → [-PWM_RANGE, PWM_RANGE] ── */
    int pwm[4];
    for (int i = 0; i < 4; i++) {
        pwm[i] = (int)(raw[i] * PWM_RANGE);
    }

    chassis_log(CHASSIS_LOG_DEBUG,
                "PWM: FL=%d  FR=%d  RL=%d  RR=%d",
                pwm[0], pwm[1], pwm[2], pwm[3]);

    /* ── Apply to motors ── */
    set_motor(g_pins[FL_IN1], g_pins[FL_IN2], pwm[0]);
    set_motor(g_pins[FR_IN1], g_pins[FR_IN2], pwm[1]);
    set_motor(g_pins[RL_IN1], g_pins[RL_IN2], pwm[2]);
    set_motor(g_pins[RR_IN1], g_pins[RR_IN2], pwm[3]);

    return 0;
}
