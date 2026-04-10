# ────────────────────────────────────────────────────────────────
#  Makefile for libchassis.so — Mecanum Wheel Chassis Library
# ────────────────────────────────────────────────────────────────

CC      := gcc
CFLAGS  := -Wall -Wextra -O2 -fPIC
TARGET  := libchassis.so
SRC     := chassis.c
HDR     := chassis.h
TEST_SRC := test_chassis.c
TEST_BIN := test_chassis

# ── Architecture detection ──
ARCH := $(shell uname -m)

ifeq ($(ARCH),aarch64)
    # Orange Pi / ARM64: link real wiringOP
    LDFLAGS := -shared -lwiringPi -lpthread -lm
    LDFLAGS_TEST := -L. -lchassis -lwiringPi -lpthread -lm -Wl,-rpath,.
else
    # x86 / dev host: mock mode, no wiringPi dependency
    LDFLAGS := -shared -lpthread -lm
    LDFLAGS_TEST := -L. -lchassis -lpthread -lm -Wl,-rpath,.
endif

# ── Default target ──
.PHONY: all clean test

all: $(TARGET)

$(TARGET): $(SRC) $(HDR)
	$(CC) $(CFLAGS) -o $@ $(SRC) $(LDFLAGS)

# ── Build & run test program ──
test: $(TARGET) $(TEST_SRC)
	$(CC) $(CFLAGS) -o $(TEST_BIN) $(TEST_SRC) $(LDFLAGS_TEST)
	@echo "──────────── Running test ────────────"
	./$(TEST_BIN)

clean:
	rm -f $(TARGET) $(TEST_BIN)
