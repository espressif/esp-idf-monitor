# SPDX-FileCopyrightText: 2023-2025 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import json
import os
from typing import Optional  # noqa: F401

IDF_PATH = os.getenv('IDF_PATH', '')
ESP_ROM_ELF_DIR = os.getenv('ESP_ROM_ELF_DIR', '')
# 'tools/idf_py_actions/roms.json' is used for compatibility with ESP-IDF before v5.5, when the file was moved
ROMS_JSON = [os.path.join(IDF_PATH, 'components', 'esp_rom', 'roms.json'), os.path.join(IDF_PATH, 'tools', 'idf_py_actions', 'roms.json')]


def get_rom_elf_path(target, chip_rev):  # type: (str, int) -> Optional[str]
    if not IDF_PATH or not ESP_ROM_ELF_DIR:  # The utility is running out of IDF
        return None
    target_roms = None

    for roms_json_path in ROMS_JSON:
        try:
            with open(roms_json_path, 'r') as file:
                target_roms = json.load(file).get(target, [])
            break
        except FileNotFoundError:
            continue

    if not target_roms:  # No suitable ROMs found
        return None

    for rom in target_roms:
        if rom.get('rev') == chip_rev:
            return os.path.join(ESP_ROM_ELF_DIR, f'{target}_rev{chip_rev}_rom.elf')

    return None
