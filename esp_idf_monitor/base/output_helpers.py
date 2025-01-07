# SPDX-FileCopyrightText: 2015-2024 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import re
import sys
from typing import Optional

# ANSI terminal codes (if changed, regular expressions in LineMatcher need to be updated)
ANSI_RED = '\033[1;31m'
ANSI_GREEN = '\033[0;32m'
ANSI_YELLOW = '\033[0;33m'
ANSI_NORMAL = '\033[0m'

# byte representations prepared for reducing the number of encoding during runtime
ANSI_RED_B = ANSI_RED.encode()
ANSI_GREEN_B = ANSI_GREEN.encode()
ANSI_YELLOW_B = ANSI_YELLOW.encode()
ANSI_NORMAL_B = ANSI_NORMAL.encode()

AUTO_COLOR_REGEX = re.compile(rb'^(I|W|E) \([\d:\. -]+\)')

COMMON_PREFIX = '---'


def color_print(message: str, color: str, newline: Optional[str] = '\n') -> None:
    """ Print a message to stderr with colored highlighting """
    sys.stderr.write('%s%s%s%s' % (color, message, ANSI_NORMAL, newline))
    sys.stderr.flush()


def normal_print(message: str) -> None:
    sys.stderr.write(ANSI_NORMAL + message)


def yellow_print(message: str, newline: Optional[str] = '\n') -> None:
    color_print(message, ANSI_YELLOW, newline)


def green_print(message: str, newline: Optional[str] = '\n') -> None:
    color_print(message, ANSI_GREEN, newline)


def red_print(message: str, newline: Optional[str] = '\n') -> None:
    color_print(message, ANSI_RED, newline)


def add_common_prefix(message: str, prefix: str = COMMON_PREFIX) -> str:
    """Add a common prefix to each line of the message if the line is not empty"""
    return ''.join(f'{prefix} {line}' if line.strip() else line for line in message.splitlines(keepends=True))


def note_print(message: str, newline: Optional[str] = '\n', prefix: str = '') -> None:
    message = add_common_prefix(message)
    yellow_print(f'{prefix}{message}', newline=newline)


def warning_print(message: str, newline: Optional[str] = '\n', prefix: str = '') -> None:
    message = add_common_prefix(message, prefix=f'{COMMON_PREFIX} Warning:')
    yellow_print(f'{prefix}{message}', newline=newline)


def error_print(message: str, newline: Optional[str] = '\n', prefix: str = '') -> None:
    message = add_common_prefix(message, prefix=f'{COMMON_PREFIX} Error:')
    red_print(f'{prefix}{message}', newline=newline)
