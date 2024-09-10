# SPDX-FileCopyrightText: 2023-2024 Espressif Systems (Shanghai) CO LTD,
# other contributors as noted.
#
# SPDX-License-Identifier: Apache-2.0

import configparser
import os

from esp_idf_monitor.base.output_helpers import note_print

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
    'reconnect_delay',
    'custom_reset_sequence',  # from esptool config
]


class Config:
    CONFIG_FILENAMES = ['esp-idf-monitor.cfg', 'config.cfg', 'tox.ini']

    def __init__(self, config_name='esp-idf-monitor', env_path='ESP_IDF_MONITOR_CFGFILE') -> None:
        self.config_name = config_name
        self.env_var_name = env_path

    def validate_configuration(self, file_path, verbose=False):
        if not os.path.exists(file_path):
            return False

        config = configparser.RawConfigParser()
        try:
            config.read(file_path, encoding='UTF-8')
            # Check if config has a [esp-idf-monitor] section to determine validity
            if config.has_section(self.config_name):
                if verbose:
                    unknown_options = list(set(config.options(self.config_name)) - set(VALID_OPTIONS))
                    unknown_options.sort()
                    if len(unknown_options) != 0:
                        note_print(f"Ignoring unknown configuration options: {', '.join(unknown_options)}")
                return True
        except (UnicodeDecodeError, configparser.Error) as e:
            if verbose:
                note_print(f'Ignoring invalid configuration file {file_path}: {e}')
        return False

    def find_configuration_file(self, dir_path, verbose=False):
        for file_name in self.CONFIG_FILENAMES:
            config_path = os.path.join(dir_path, file_name)
            if self.validate_configuration(config_path, verbose):
                return config_path
        return None

    def load_configuration(self, verbose=False):
        set_with_env_var = False
        env_var_path = os.environ.get(self.env_var_name)
        if env_var_path is not None and self.validate_configuration(env_var_path, self.config_name):
            config_file_path = env_var_path
            set_with_env_var = True
        else:
            home_dir = os.path.expanduser('~')
            os_config_dir = (
                f'{home_dir}/.config/{self.config_name}'
                if os.name == 'posix'
                else f'{home_dir}/AppData/Local/{self.config_name}/'
            )
            # Search priority: 1) current directory, 2) OS-specific config directory, 3) home directory
            for dir_path in (os.getcwd(), os_config_dir, home_dir):
                config_file_path = self.find_configuration_file(dir_path, verbose)
                if config_file_path:
                    break

        config = configparser.ConfigParser()
        # Create an empty configuration when no file is found
        config[self.config_name] = {}

        if config_file_path is not None:
            # If a configuration file is found and validated, read and parse it
            config.read(config_file_path)
            if verbose:
                msg = f' (set with {self.env_var_name} environment variable)' if set_with_env_var else ''
                note_print(
                    f'Loaded custom configuration from {os.path.abspath(config_file_path)}{msg}'
                )
        return config, config_file_path
