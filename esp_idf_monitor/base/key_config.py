# SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

from typing import Optional

from esp_idf_monitor.config import Config

from .output_helpers import error_print

cfg, _ = Config().load_configuration()
cfg = cfg['esp-idf-monitor']


def key_to_hex(key: Optional[str], default: str) -> str:
    if key is None:
        key = default
    ascii_code = ord(key.upper())
    # convert key from string to C0 escape code
    C0_code = ascii_code - ord('@')
    if C0_code < 1 or C0_code > 32:
        error_print(f"Unsupported configuration for key: '{key}', please use just the English alphabet "
                    f"characters (A-Z) and [,],\\,^,_. Using the default option '{default}'.")
        C0_code = ord(default) - ord('@')
    return chr(C0_code)


MENU_KEY = key_to_hex(cfg.get('menu_key'), 'T')
EXIT_KEY = key_to_hex(cfg.get('exit_key'), ']')
CHIP_RESET_KEY = key_to_hex(cfg.get('chip_reset_key'), 'R')
RECOMPILE_UPLOAD_KEY = key_to_hex(cfg.get('recompile_upload_key'), 'F')
RECOMPILE_UPLOAD_APP_KEY = key_to_hex(cfg.get('recompile_upload_app_key'), 'A')
TOGGLE_OUTPUT_KEY = key_to_hex(cfg.get('toggle_output_key'), 'Y')
TOGGLE_LOG_KEY = key_to_hex(cfg.get('toggle_log_key'), 'L')
TOGGLE_TIMESTAMPS_KEY = key_to_hex(cfg.get('toggle_timestamp_key'), 'I')
CHIP_RESET_BOOTLOADER_KEY = key_to_hex(cfg.get('chip_reset_bootloader_key'), 'P')
EXIT_MENU_KEY = key_to_hex(cfg.get('exit_menu_key'), 'X')
SKIP_MENU_KEY = cfg.get('skip_menu_key', False)


COMMAND_KEYS = [
    CHIP_RESET_KEY,
    RECOMPILE_UPLOAD_KEY,
    RECOMPILE_UPLOAD_APP_KEY,
    TOGGLE_OUTPUT_KEY,
    TOGGLE_LOG_KEY,
    TOGGLE_TIMESTAMPS_KEY,
    CHIP_RESET_BOOTLOADER_KEY,
    EXIT_MENU_KEY,
    SKIP_MENU_KEY
]
