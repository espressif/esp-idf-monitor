# SPDX-FileCopyrightText: 2015-2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
# This file contains values (e.g. delay time, ...) that are different for each chip for a particular action.
# If adding a new device, set only values that are different from the default, e.g.:
#                                       'esp32s2': {
#                                               0: {
#                                                   'reset': 0.35,
#                                               }
#                                           },
#
# for more information see the method "handle_commands" in idf_monitor.py

from .constants import MINIMAL_EN_LOW_DELAY

conf = {
    # the default values were previously hardcoded in idf_monitor.py (taken from esptool.py)
    'default': {
        0: {
            'reset': MINIMAL_EN_LOW_DELAY,
            'enter_boot_set': 0.1,
            'enter_boot_unset': 0.05,
        }
    },
    'esp32': {
        0: {
            'reset': MINIMAL_EN_LOW_DELAY,
            'enter_boot_set': 1.3,
            'enter_boot_unset': 0.45,
        },
        100: {
            'reset': MINIMAL_EN_LOW_DELAY,
            'enter_boot_set': 0.1,
            'enter_boot_unset': 0.05,
        }
    },

}


def get_chip_config(chip, revision=0):
    # type: (str, int) -> dict

    # If the config is not set in the `conf` dict for a specific chip, the `default` will be used.
    # In case if only some values are specified, others are used from the `default`.
    # If chip is set in `conf` but the specific revision R is missing,
    # the values from highest revision lower than R are used.
    # If some fields are missing, they will be taken from next lower revision or from the `default`.
    default = dict(conf['default'][0])
    rev_number = int(revision)
    if chip not in conf.keys():
        return default
    chip_revisions = sorted(list(conf[chip].keys()), key=int)
    for rev in chip_revisions:
        if int(rev) > rev_number:
            break
        for key in conf[chip][rev].keys():
            default[key] = conf[chip][rev][key]

    return default
