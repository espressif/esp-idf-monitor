# SPDX-FileCopyrightText: 2015-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

from esp_idf_monitor.base.key_config import cfg

# Control-key characters
CTRL_C = '\x03'
CTRL_H = '\x08'

# VT100 escape sequences
CONSOLE_STATUS_QUERY = b'\x1b[5n'

# Command parsed from console inputs
CMD_STOP = 1
CMD_RESET = 2
CMD_MAKE = 3
CMD_APP_FLASH = 4
CMD_OUTPUT_TOGGLE = 5
CMD_TOGGLE_LOGGING = 6
CMD_ENTER_BOOT = 7
CMD_TOGGLE_TIMESTAMPS = 8
CMD_FLASH_ALL = 9

# Process exit code of a script that timed out waiting for 'expect' in
# Command-Stream Mode, so that CI can tell it apart from a clean run.
# This is ETIMEDOUT on Linux, hardcoded on purpose: errno.ETIMEDOUT differs
# per platform (110 on Linux, 60 on macOS, 138 on Windows) and a script
# checking the exit code has to work the same everywhere.
EXIT_EXPECT_TIMEOUT = 110

# Generic non-zero exit code for script errors (invalid regex, bad --timeout
# arguments, etc.) in Command-Stream Mode. Matches the convention used by
# argparse, grep, and bash for usage/syntax errors.
EXIT_SCRIPT_ERROR = 2

# Tags for tuples in queues
TAG_KEY = 0
TAG_SERIAL = 1
TAG_SERIAL_FLUSH = 2
TAG_CMD = 3

DEFAULT_TOOLCHAIN_PREFIX = 'xtensa-esp32-elf-'

DEFAULT_PRINT_FILTER = ''
DEFAULT_TARGET_RESET = True

# panic handler related messages
PANIC_START = r'Core \s*\d+ register dump:'
PANIC_END = b'ELF file SHA256:'
PANIC_STACK_DUMP = b'Stack memory:'

# panic handler decoding states
PANIC_IDLE = 0
PANIC_READING = 1
PANIC_READING_STACK = 2

# panic handler decoding options
PANIC_DECODE_DISABLE = 'disable'
PANIC_DECODE_BACKTRACE = 'backtrace'

EVENT_QUEUE_TIMEOUT = 0.03  # timeout before raising queue.Empty exception in case of empty event queue

ESPPORT_ENVIRON = 'ESPPORT'
ESPTOOL_OPEN_PORT_ATTEMPTS_ENVIRON = 'ESPTOOL_OPEN_PORT_ATTEMPTS'
MAKEFLAGS_ENVIRON = 'MAKEFLAGS'

GDB_UART_CONTINUE_COMMAND = '+$c#63'
GDB_EXIT_TIMEOUT = 0.3  # time delay between exit and writing GDB_UART_CONTINUE_COMMAND

# workaround for data sent without EOL
# if no data received during the time, last line is considered finished
LAST_LINE_THREAD_INTERVAL = 0.1

RECONNECT_DELAY = cfg.getint('reconnect_delay', 0.5)  # timeout between reconnect tries
CHECK_ALIVE_FLAG_TIMEOUT = 0.25  # timeout for checking alive flags (currently used by serial reader)

# closing wait timeout for serial port
ASYNC_CLOSING_WAIT_NONE = 0xFFFF  # don't wait at all
