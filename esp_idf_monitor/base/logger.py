# SPDX-FileCopyrightText: 2015-2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import datetime
import os
from typing import AnyStr, BinaryIO, Callable, List, Optional  # noqa: F401

from esp_idf_panic_decoder import PcAddressDecoder
from serial.tools import miniterm

from esp_idf_monitor.base.key_config import MENU_KEY, TOGGLE_OUTPUT_KEY

from .output_helpers import error_print, note_print

key_description = miniterm.key_description


class Logger:
    def __init__(self, elf_files, console, timestamps, timestamp_format, enable_address_decoding,
                 toolchain_prefix, rom_elf_file=None):
        # type: (List[str], miniterm.Console, bool, str, bool, str, Optional[str]) -> None
        self.log_file = None  # type: Optional[BinaryIO]
        self._output_enabled = True  # type: bool
        self._start_of_line = True  # type: bool
        self.elf_file = elf_files[0] if elf_files else ''
        self.console = console
        self.timestamps = timestamps
        self.timestamp_format = timestamp_format
        if enable_address_decoding:
            self.pc_address_decoder = PcAddressDecoder(toolchain_prefix, elf_files, rom_elf_file)

    @property
    def pc_address_buffer(self) -> bytes:
        return getattr(self.pc_address_decoder, 'pc_address_buffer', b'')

    @pc_address_buffer.setter
    def pc_address_buffer(self, value: bytes) -> None:
        if self.pc_address_decoder:
            self.pc_address_decoder.pc_address_buffer = value

    @property
    def output_enabled(self):  # type: () -> bool
        return self._output_enabled

    @output_enabled.setter
    def output_enabled(self, value):  # type: (bool) -> None
        self._output_enabled = value

    @property
    def log_file(self):  # type: () -> Optional[BinaryIO]
        return self._log_file

    @log_file.setter
    def log_file(self, value):  # type: (Optional[BinaryIO]) -> None
        self._log_file = value

    def toggle_logging(self):  # type: () -> None
        if self._log_file:
            self.stop_logging()
        else:
            self.start_logging()

    def toggle_timestamps(self):  # type: () -> None
        self.timestamps = not self.timestamps

    def start_logging(self):  # type: () -> None
        if not self._log_file:
            name = 'log.{}.{}.txt'.format(os.path.splitext(os.path.basename(self.elf_file))[0],
                                          datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
            try:
                self.log_file = open(name, 'wb+')
                note_print(f'Logging is enabled into file {name}', prefix='\n')
            except Exception as e:  # noqa
                error_print(f'Log file {name} cannot be created: {e}', prefix='\n')

    def stop_logging(self):  # type: () -> None
        if self._log_file:
            try:
                name = self._log_file.name
                self._log_file.close()
                note_print(f'Logging is disabled and file {name} has been closed', prefix='\n')
            except Exception as e:  # noqa
                error_print(f'Log file cannot be closed: {e}', prefix='\n')
            finally:
                self._log_file = None

    def print(self, string, console_printer=None):
        # type: (AnyStr, Optional[Callable]) -> None
        if console_printer is None:
            console_printer = self.console.write_bytes

        if isinstance(string, type(u'')):
            new_line_char = '\n'
        else:
            new_line_char = b'\n'  # type: ignore

        if string and self.timestamps and (self._output_enabled or self._log_file):
            t = datetime.datetime.now().strftime(self.timestamp_format)

            # "string" is not guaranteed to be a full line. Timestamps should be only at the beginning of lines.
            if isinstance(string, type(u'')):
                line_prefix = t + ' '
            else:
                line_prefix = t.encode('ascii') + b' '  # type: ignore

            # If the output is at the start of a new line, prefix it with the timestamp text.
            if self._start_of_line:
                string = line_prefix + string

            # If the new output ends with a newline, remove it so that we don't add a trailing timestamp.
            self._start_of_line = string.endswith(new_line_char)
            if self._start_of_line:
                string = string[:-len(new_line_char)]

            string = string.replace(new_line_char, new_line_char + line_prefix)

            # If we're at the start of a new line again, restore the final newline.
            if self._start_of_line:
                string += new_line_char
        elif string:
            self._start_of_line = string.endswith(new_line_char)

        if self._output_enabled:
            console_printer(string)
        if self._log_file:
            try:
                if isinstance(string, type(u'')):
                    string = string.encode()  # type: ignore
                self._log_file.write(string)  # type: ignore
            except Exception as e:
                error_print(f'Cannot write to file: {e}', prefix='\n')
                # don't fill-up the screen with the previous errors (probably consequent prints would fail also)
                self.stop_logging()

    def output_toggle(self):  # type: () -> None
        self.output_enabled = not self.output_enabled
        note_print(f'Toggle output display: {self.output_enabled}, '
                   f'Type {key_description(MENU_KEY)} {key_description(TOGGLE_OUTPUT_KEY)} '
                   'to show/disable output again.', prefix='\n')

    def handle_possible_pc_address_in_line(self, line: bytes, insert_new_line: bool = False) -> None:
        if not self.pc_address_decoder:
            return
        translation = self.pc_address_decoder.decode_address(line)
        if translation:
            if insert_new_line:
                # insert a new line in case address translation is printed in the middle of a line
                self.print(b'\n')
            self.print(translation, console_printer=note_print)
