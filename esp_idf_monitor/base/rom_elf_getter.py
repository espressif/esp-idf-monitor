# SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import json
import os
from typing import Optional  # noqa: F401

IDF_PATH = os.getenv('IDF_PATH', '')
ESP_ROM_ELF_DIR = os.getenv('ESP_ROM_ELF_DIR', '')
ROMS_JSON = os.path.join(IDF_PATH, 'tools', 'idf_py_actions', 'roms.json')  # type: ignore


def get_rom_elf_path(target, chip_rev):  # type: (str, int) -> Optional[str]
    if not IDF_PATH or not ESP_ROM_ELF_DIR:  # The utility is running out of IDF
        return None

    with open(ROMS_JSON, 'r') as file:
        target_roms = json.load(file).get(target, [])

    if not target_roms:  # No suitable ROMs found
        return None

    for rom in target_roms:
        if rom.get('rev') == chip_rev:
            return os.path.join(ESP_ROM_ELF_DIR, f'{target}_rev{chip_rev}_rom.elf')  # type: ignore

    return None
