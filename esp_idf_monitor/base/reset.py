# SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import os
import struct
import time
from typing import Optional

import serial
from serial.tools import list_ports

from esp_idf_monitor.base.chip_specific_config import get_chip_config
from esp_idf_monitor.base.constants import HIGH, LOW, USB_JTAG_SERIAL_PID
from esp_idf_monitor.base.output_helpers import error_print, note_print
from esp_idf_monitor.config import Config

if os.name != 'nt':
    import fcntl
    import termios

    # Constants used for terminal status lines reading/setting.
    # Taken from pySerial's backend for IO:
    # https://github.com/pyserial/pyserial/blob/master/serial/serialposix.py
    TIOCMSET = getattr(termios, 'TIOCMSET', 0x5418)
    TIOCMGET = getattr(termios, 'TIOCMGET', 0x5415)
    TIOCM_DTR = getattr(termios, 'TIOCM_DTR', 0x002)
    TIOCM_RTS = getattr(termios, 'TIOCM_RTS', 0x004)


class Reset:

    format_dict = {
        'D': 'self._setDTR({})',
        'R': 'self._setRTS({})',
        'W': 'time.sleep({})',
        'U': 'self._setDTRandRTS({})',
    }

    def __init__(self, serial_instance: serial.Serial, chip: str) -> None:
        self.serial_instance = serial_instance
        self.chip_config = get_chip_config(chip)
        self.port_pid = self._get_port_pid()
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration for custom reset sequence
        Look for custom_reset_sequence in esp-idf-monitor config, if not found fallback to esptool config
        """
        custom_cfg = Config()
        custom_config, self.config_path = custom_cfg.load_configuration()
        # try to get the custom reset sequence from esp-idf-monitor
        self.esptool_config = False
        self.custom_seq = custom_config['esp-idf-monitor'].get('custom_reset_sequence')
        if self.config_path is None:
            # config for esp-idf-monitor was not found, looking for esptool configuration
            # this is required in case the config file doesn't contain esp-idf-monitor section at all
            custom_cfg = Config(config_name='esptool')
            custom_config, self.config_path = custom_cfg.load_configuration()
        if self.custom_seq is None and 'esptool' in custom_config.keys():
            # get reset sequence from esptool section
            self.custom_seq = custom_config['esptool'].get('custom_reset_sequence')
            self.esptool_config = True

    def _get_port_pid(self) -> Optional[int]:
        """Get port PID to differentiate between JTAG and UART reset sequences"""
        if not hasattr(self.serial_instance, 'port'):
            # Linux target serial does not have a port and thus does not support resseting
            return None
        for port in list_ports.comports():
            if port.device == self.serial_instance.port:
                return port.pid  # type: ignore
        return None  # port not found in connected ports

    def _setDTR(self, value: bool) -> None:
        """Wrapper for setting DTR"""
        self.serial_instance.setDTR(value)

    def _setRTS(self, value: bool) -> None:
        """Wrapper for setting RTS with workaround for Windows"""
        self.serial_instance.setRTS(value)
        self.serial_instance.setDTR(self.serial_instance.dtr)  # usbser.sys workaround

    def _setDTRandRTS(self, dtr: bool = HIGH, rts: bool = HIGH) -> None:
        """Set DTR and RTS at the same time, this is only supported on linux with custom reset sequence"""
        if not self.serial_instance.is_open:
            error_print('Serial port is not connected')
            return None
        status = struct.unpack(
            'I', fcntl.ioctl(self.serial_instance.fileno(), TIOCMGET, struct.pack('I', 0))
        )[0]
        if dtr:
            status |= TIOCM_DTR
        else:
            status &= ~TIOCM_DTR
        if rts:
            status |= TIOCM_RTS
        else:
            status &= ~TIOCM_RTS
        fcntl.ioctl(self.serial_instance.fileno(), TIOCMSET, struct.pack('I', status))

    def _parse_string_to_seq(self, seq_str: str) -> str:
        """Parse custom reset sequence from a config"""
        try:
            cmds = seq_str.split('|')
            fn_calls_list = [self.format_dict[cmd[0]].format(cmd[1:]) for cmd in cmds]
        except Exception as e:
            error_print(f'Invalid "custom_reset_sequence" option format: {e}')
            return ''
        return '\n'.join(fn_calls_list)

    def hard(self) -> None:
        """Hard reset chip"""
        self._setRTS(LOW)  # EN=LOW, chip in reset
        time.sleep(self.chip_config['reset'])
        self._setRTS(HIGH)  # EN=HIGH, chip out of reset

    def to_bootloader(self) -> None:
        """Reset chip into bootloader"""
        if self.custom_seq:
            # use custom reset sequence set in config file
            source = 'from esptool ' if self.esptool_config else ''
            note_print(f'Using custom reset sequence {source}config file: {self.config_path}')
            exec(self._parse_string_to_seq(self.custom_seq))
        elif self.port_pid == USB_JTAG_SERIAL_PID:
            # use reset sequence for JTAG
            self._setRTS(HIGH)
            self._setDTR(HIGH)  # Idle
            time.sleep(0.1)
            self._setDTR(LOW)  # Set IO0
            self._setRTS(HIGH)
            time.sleep(0.1)
            self._setRTS(LOW)  # Reset.
            self._setDTR(HIGH)
            self._setRTS(LOW)  # RTS set as Windows only propagates DTR on RTS setting
            time.sleep(0.1)
            self._setDTR(HIGH)
            self._setRTS(HIGH)  # Chip out of reset
        else:
            # use traditional reset sequence
            self._setDTR(HIGH)  # IO0=HIGH
            self._setRTS(LOW)  # EN=LOW, chip in reset
            time.sleep(self.chip_config['enter_boot_set'])  # timeouts taken from esptool.py, includes esp32r0 workaround. defaults: 0.1
            self._setDTR(LOW)  # IO0=LOW
            self._setRTS(HIGH)  # EN=HIGH, chip out of reset
            time.sleep(self.chip_config['enter_boot_unset'])  # timeouts taken from esptool.py, includes esp32r0 workaround. defaults: 0.05
            self._setDTR(HIGH)  # IO0=HIGH, done
