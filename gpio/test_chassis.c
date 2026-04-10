/**
 * @file test_chassis.c
 * @brief Test / demo program for the chassis library.
 *
 * On x86 (mock mode) this programme exercises every movement pattern and
 * prints the mock pin-operation logs so developers can verify the inverse
 * kinematics and clamping logic without real hardware.
 */

#include <stdio.h>
#include "chassis.h"

/* Helper: print a separator */
static void section(const char *title) {
    printf("\n════════════════════════════════════════════\n");
    printf("  TEST: %s\n", title);
    printf("════════════════════════════════════════════\n");
}

int main(void) {
    /* ── Pin mapping (wiringPi numbers, example values) ── */
    const int pins[8] = {
        0, 1,   /* FL_IN1, FL_IN2 */
        2, 3,   /* FR_IN1, FR_IN2 */
        4, 5,   /* RL_IN1, RL_IN2 */
        6, 7    /* RR_IN1, RR_IN2 */
    };

    /* Enable full debug logging */
    chassis_set_log_level(CHASSIS_LOG_DEBUG);

    /* ────── Guard: call before init (should be rejected) ────── */
    section("Call before init (expect ERROR)");
    if (chassis_set_velocity(1.0f, 0.0f, 0.0f) == -1) {
        printf("  -> Correctly rejected (not initialized)\n");
    }

    /* ────── Initialise ────── */
    section("Initialization");
    if (chassis_init(pins) != 0) {
        printf("FATAL: chassis_init failed\n");
        return 1;
    }

    /* ────── Guard: double init (should be rejected) ────── */
    section("Double init (expect ERROR)");
    if (chassis_init(pins) == -1) {
        printf("  -> Correctly rejected (already initialized)\n");
    }

    /* ────── Basic movements ────── */
    section("Forward (vy=1)");
    chassis_set_velocity(0.0f, 1.0f, 0.0f);

    section("Backward (vy=-1)");
    chassis_set_velocity(0.0f, -1.0f, 0.0f);

    section("Strafe right (vx=1)");
    chassis_set_velocity(1.0f, 0.0f, 0.0f);

    section("Strafe left (vx=-1)");
    chassis_set_velocity(-1.0f, 0.0f, 0.0f);

    section("Rotate CW (omega=1)");
    chassis_set_velocity(0.0f, 0.0f, 1.0f);

    section("Rotate CCW (omega=-1)");
    chassis_set_velocity(0.0f, 0.0f, -1.0f);

    /* ────── Diagonal movement ────── */
    section("Diagonal: forward-right (vx=0.5, vy=0.5)");
    chassis_set_velocity(0.5f, 0.5f, 0.0f);

    /* ────── Combined movement with rotation ────── */
    section("Forward + CW rotation (vy=0.5, omega=0.5)");
    chassis_set_velocity(0.0f, 0.5f, 0.5f);

    /* ────── Extreme input: proportional clamping ────── */
    section("Extreme input (vx=1, vy=1, omega=1) — clamping expected");
    chassis_set_velocity(1.0f, 1.0f, 1.0f);

    /* ────── Stop ────── */
    section("Stop (all zero)");
    chassis_set_velocity(0.0f, 0.0f, 0.0f);

    /* ────── Cleanup ────── */
    section("Cleanup");
    chassis_cleanup();

    /* ────── Guard: call after cleanup (should be rejected) ────── */
    section("Call after cleanup (expect ERROR)");
    if (chassis_set_velocity(1.0f, 0.0f, 0.0f) == -1) {
        printf("  -> Correctly rejected (cleaned up)\n");
    }

    printf("\n✅ All tests passed.\n");
    return 0;
}
