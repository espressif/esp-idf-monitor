# SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD,
# other contributors as noted.
#
# SPDX-License-Identifier: Apache-2.0

import configparser
import os

from esp_idf_monitor.base.output_helpers import yellow_print

VALID_OPTIONS = [
    'menu_key',
    'exit_key',
    'chip_reset_key',
    'recompile_upload_key',
    'recompile_upload_app_key',
    'toggle_output_key',
    'toggle_log_key',
    'toggle_timestamp_key',
    'chip_reset_bootloader_key',
    'exit_menu_key',
    'skip_menu_key',
]
CONFIG_FILENAMES = ['esp-idf-monitor.cfg', 'config.cfg', 'tox.ini']


def validate_configuration(file_path, verbose=False):
    if not os.path.exists(file_path):
        return False

    config = configparser.RawConfigParser()
    try:
        config.read(file_path, encoding='UTF-8')
        # Check if config has a [esp-idf-monitor] section to determine validity
        if config.has_section('esp-idf-monitor'):
            if verbose:
                unknown_options = list(set(config.options('esp-idf-monitor')) - set(VALID_OPTIONS))
                unknown_options.sort()
                if len(unknown_options) != 0:
                    yellow_print(f"--- Ignoring unknown configuration options: {', '.join(unknown_options)}")
            return True
    except (UnicodeDecodeError, configparser.Error) as e:
        if verbose:
            yellow_print(f'--- Ignoring invalid configuration file {file_path}: {e}')
    return False


def find_configuration_file(dir_path, verbose=False):
    for file_name in CONFIG_FILENAMES:
        config_path = os.path.join(dir_path, file_name)
        if validate_configuration(config_path, verbose):
            return config_path
    return None


def load_configuration(verbose=False):
    set_with_env_var = False
    env_var_path = os.environ.get('ESP_IDF_MONITOR_CFGFILE')
    if env_var_path is not None and validate_configuration(env_var_path):
        config_file_path = env_var_path
        set_with_env_var = True
    else:
        home_dir = os.path.expanduser('~')
        os_config_dir = (
            f'{home_dir}/.config/esp-idf-monitor'
            if os.name == 'posix'
            else f'{home_dir}/AppData/Local/esp-idf-monitor/'
        )
        # Search priority: 1) current directory, 2) OS-specific config directory, 3) home directory
        for dir_path in (os.getcwd(), os_config_dir, home_dir):
            config_file_path = find_configuration_file(dir_path, verbose)
            if config_file_path:
                break

    config = configparser.ConfigParser()
    # Create an empty configuration when no file is found
    config['esp-idf-monitor'] = {}

    if config_file_path is not None:
        # If a configuration file is found and validated, read and parse it
        config.read(config_file_path)
        if verbose:
            msg = ' (set with ESP_IDF_MONITOR_CFGFILE environment variable)' if set_with_env_var else ''
            yellow_print(
                f'--- Loaded custom configuration from {os.path.abspath(config_file_path)}{msg}'
            )
    return config, config_file_path
