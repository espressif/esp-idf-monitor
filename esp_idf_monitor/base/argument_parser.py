# SPDX-FileCopyrightText: 2015-2024 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import argparse
import os
import sys

from .constants import (DEFAULT_PRINT_FILTER, DEFAULT_TARGET_RESET,
                        DEFAULT_TOOLCHAIN_PREFIX, PANIC_DECODE_BACKTRACE,
                        PANIC_DECODE_DISABLE)
from .coredump import COREDUMP_DECODE_DISABLE, COREDUMP_DECODE_INFO


def get_parser():  # type: () -> argparse.ArgumentParser
    parser = argparse.ArgumentParser('idf_monitor - a serial output monitor for esp-idf')

    parser.add_argument(
        '--port', '-p',
        help='Serial port device. If not set, a connected port will be used.' +
        (' Defaults to `/dev/ttyUSB0` if connected.' if sys.platform == 'linux' else ''),
        default=os.environ.get('ESPTOOL_PORT', None)
    )

    parser.add_argument(
        '--no-reset',
        help='Do not reset the chip on monitor startup',
        action='store_true',
        default=bool(os.getenv('ESP_IDF_MONITOR_NO_RESET', not DEFAULT_TARGET_RESET))
    )

    parser.add_argument(
        '--disable-address-decoding', '-d',
        help="Don't print lines about decoded addresses from the application ELF file",
        action='store_true',
        default=os.getenv('ESP_MONITOR_DECODE') == '0'
    )

    parser.add_argument(
        '--baud', '-b',
        help='Serial port baud rate',
        type=int,
        default=os.getenv('IDF_MONITOR_BAUD', os.getenv('MONITORBAUD', 115200)))

    parser.add_argument(
        '--make', '-m',
        help='Command to run make',
        type=str, default='make')

    parser.add_argument(
        '--encrypted',
        help='Use encrypted targets while running make',
        action='store_true')

    parser.add_argument(
        '--toolchain-prefix',
        help='Triplet prefix to add before cross-toolchain names',
        default=DEFAULT_TOOLCHAIN_PREFIX)

    parser.add_argument(
        '--eol',
        choices=['CR', 'LF', 'CRLF'],
        type=lambda c: c.upper(),
        help='End of line to use when sending to the serial port. '
             'Defaults to LF for Linux targets and CR otherwise.',
        )

    parser.add_argument(
        'elf_files', help='ELF files of application, bootloader, etc. '
        'Please note that the order of the files also defines order in which they are used for address decoding.',
        type=str,
        nargs='*'
    )

    parser.add_argument(
        '--rom-elf-file',
        help='ELF file of target ROM for address decoding. '
        'If not specified, autodetection is attempted based on the IDF_PATH and ESP_ROM_ELF_DIR env vars.',
        type=lambda f: open(f, 'rb') if os.path.exists(f) else f'{f}',
    )

    parser.add_argument(
        '--print_filter',
        help='Filtering string',
        default=os.environ.get('ESP_IDF_MONITOR_PRINT_FILTER', DEFAULT_PRINT_FILTER))

    parser.add_argument(
        '--decode-coredumps',
        choices=[COREDUMP_DECODE_INFO, COREDUMP_DECODE_DISABLE],
        default=COREDUMP_DECODE_INFO,
        help='Handling of core dumps found in serial output'
    )

    parser.add_argument(
        '--decode-panic',
        choices=[PANIC_DECODE_BACKTRACE, PANIC_DECODE_DISABLE],
        default=PANIC_DECODE_DISABLE,
        help='Handling of panic handler info found in serial output'
    )

    parser.add_argument(
        '--target',
        help='Target name (used when stack dump decoding is enabled)',
        default=os.environ.get('IDF_TARGET', 'esp32')
    )

    parser.add_argument(
        '--revision',
        help='Revision of the target',
        type=int,
        default=0
    )

    parser.add_argument(
        '--ws',
        default=os.environ.get('ESP_IDF_MONITOR_WS', None),
        help='WebSocket URL for communicating with IDE tools for debugging purposes'
    )

    parser.add_argument(
        '--timestamps',
        help='Add timestamp for each line',
        default=False,
        action='store_true')

    parser.add_argument(
        '--timestamp-format',
        default=os.environ.get('ESP_IDF_MONITOR_TIMESTAMP_FORMAT', '%Y-%m-%d %H:%M:%S'),
        help='Set a strftime()-compatible timestamp format'
    )

    parser.add_argument(
        '--force-color',
        help='Always colored monitor output, even if output is redirected.',
        default=False,
        action='store_true')

    parser.add_argument(
        '--disable-auto-color',
        help='Disable automatic color addition to monitor output based on the log level',
        default=False,
        action='store_true')

    parser.add_argument(
        '--open-port-attempts',
        help='Number of attempts to wait for the port to appear (useful if the device is not connected or in deep sleep). '
        'The delay between attempts can be defined by the `reconnect_delay` option in a configuration file (by default 0.5 sec). '
        'Use 0 for infinite attempts.',
        default=1,
        type=int
    )

    return parser
