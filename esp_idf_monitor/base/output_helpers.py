# SPDX-FileCopyrightText: 2015-2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import sys
from typing import Optional  # noqa: F401

# ANSI terminal codes (if changed, regular expressions in LineMatcher need to be updated)
ANSI_RED = '\033[1;31m'
ANSI_YELLOW = '\033[0;33m'
ANSI_NORMAL = '\033[0m'


def color_print(message, color, newline='\n'):  # type: (str, str, Optional[str]) -> None
    """ Print a message to stderr with colored highlighting """
    sys.stderr.write('%s%s%s%s' % (color, message, ANSI_NORMAL, newline))
    sys.stderr.flush()


def normal_print(message):  # type: (str) -> None
    sys.stderr.write(ANSI_NORMAL + message)


def yellow_print(message, newline='\n'):  # type: (str, Optional[str]) -> None
    color_print(message, ANSI_YELLOW, newline)


def red_print(message, newline='\n'):  # type: (str, Optional[str]) -> None
    color_print(message, ANSI_RED, newline)
