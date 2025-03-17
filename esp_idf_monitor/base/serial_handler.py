# SPDX-FileCopyrightText: 2015-2025 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import hashlib
import os
import queue  # noqa: F401
import re
import subprocess
from typing import Callable, List, Optional  # noqa: F401

import serial  # noqa: F401
from esp_idf_panic_decoder import PanicOutputDecoder
from serial.tools import miniterm  # noqa: F401

from .binlog import BinaryLog
from .console_parser import (ConsoleParser, key_description,  # noqa: F401
                             prompt_next_action)
from .console_reader import ConsoleReader  # noqa: F401
from .constants import (CMD_APP_FLASH, CMD_ENTER_BOOT, CMD_MAKE,
                        CMD_OUTPUT_TOGGLE, CMD_RESET, CMD_STOP,
                        CMD_TOGGLE_LOGGING, CMD_TOGGLE_TIMESTAMPS,
                        CONSOLE_STATUS_QUERY, PANIC_DECODE_DISABLE, PANIC_END,
                        PANIC_IDLE, PANIC_READING, PANIC_READING_STACK,
                        PANIC_STACK_DUMP, PANIC_START)
from .coredump import CoreDump  # noqa: F401
from .exceptions import SerialStopException
from .gdbhelper import GDBHelper  # noqa: F401
from .key_config import CHIP_RESET_KEY, EXIT_KEY, MENU_KEY
from .line_matcher import LineMatcher  # noqa: F401
from .logger import Logger  # noqa: F401
from .output_helpers import (ANSI_GREEN_B, ANSI_NORMAL_B, ANSI_RED_B,
                             ANSI_YELLOW_B, AUTO_COLOR_REGEX, note_print,
                             warning_print)
from .reset import Reset
from .serial_reader import Reader  # noqa: F401


def get_sha256(filename, block_size=65536):  # type: (str, int) -> str
    sha256 = hashlib.sha256()
    with open(filename, 'rb') as f:
        for block in iter(lambda: f.read(block_size), b''):
            sha256.update(block)
    return sha256.hexdigest()


def run_make(target, make, console, console_parser, event_queue, cmd_queue, logger):
    # type: (str, str, miniterm.Console, ConsoleParser, queue.Queue, queue.Queue, Logger) -> None
    if isinstance(make, list):
        popen_args = make + [target]
    else:
        popen_args = [make, target]
    note_print('Running %s...' % ' '.join(popen_args))
    p = subprocess.Popen(popen_args, env=os.environ)
    try:
        p.wait()
    except KeyboardInterrupt:
        p.wait()
    if p.returncode != 0:
        prompt_next_action('Build failed', console, console_parser, event_queue, cmd_queue)
    else:
        logger.output_enabled = True


class SerialHandler:
    """
    The class is responsible for buffering serial input and performing corresponding commands.
    """
    def __init__(self, last_line_part, serial_check_exit, logger, decode_panic, reading_panic, panic_buffer, target,
                 force_line_print, start_cmd_sent, serial_instance, encrypted, elf_files, toolchain_prefix, disable_auto_color):
        # type: (bytes, bool, Logger, str, int, bytes, str, bool, bool, serial.Serial, bool, List[str], str, bool) -> None
        self._last_line_part = last_line_part
        self._serial_check_exit = serial_check_exit
        self.logger = logger
        self._decode_panic = decode_panic
        self._reading_panic = reading_panic
        self._panic_buffer = panic_buffer
        self.target = target
        self._force_line_print = force_line_print
        self.start_cmd_sent = start_cmd_sent
        self.serial_instance = serial_instance
        self.encrypted = encrypted
        self.elf_files = elf_files
        self.decode_error_cnt = 0
        self._trailing_color = False
        self.reset = Reset(serial_instance, target)
        self.panic_handler = PanicOutputDecoder(toolchain_prefix, elf_files, target)
        self.disable_auto_color = disable_auto_color
        self.binlog = BinaryLog(elf_files)
        self.binary_log_detected = False

    def splitdata(self, data):  # type: (bytes) -> List[bytes]
        """
        Split data into lines, while keeping newlines, and move unfinished line for future processing
        """
        # if data is empty fallback to empty string for easier concatenation with last line
        self.binary_log_detected = (
            self.binlog.detected(self._last_line_part[0])
            if self._last_line_part
            else self.binlog.detected(data[0]) if data
            else False
        )
        if self.binary_log_detected:
            if self._last_line_part != b'':
                data = self._last_line_part + data
                self._last_line_part = b''
            return [data]
        else:
            sp = data.splitlines(keepends=True) or [b'']
            if self._last_line_part != b'':
                # add unprocessed part from previous "data" to the first line
                sp[0] = self._last_line_part + sp[0]
                self._last_line_part = b''
            if not sp[-1].endswith(b'\n'):
                # last part is not a full line
                self._last_line_part = sp.pop()
            return sp

    def print_colored(self, line: bytes) -> None:
        if self.disable_auto_color:
            self.logger.print(line)
            return

        # Line ending can be only at the end because of the split in splitdata
        line_stripped = line.rstrip(b'\r\n')
        new_line = line[len(line_stripped):]

        match = AUTO_COLOR_REGEX.match(line_stripped)
        if not match:
            if self._trailing_color and new_line:
                # Color set in some previous call and now we have a line
                # ending, so reset the color
                self.logger.print(line_stripped + ANSI_NORMAL_B + new_line)
                self._trailing_color = False
            else:
                # No color and no need for reset, just print the line
                self.logger.print(line)
            return

        if match.group(1) == b'I':
            color = ANSI_GREEN_B
        elif match.group(1) == b'W':
            color = ANSI_YELLOW_B
        elif match.group(1) == b'E':
            color = ANSI_RED_B

        if new_line:
            # Full line, print with color and reset
            self.logger.print(color + line_stripped + ANSI_NORMAL_B + new_line)
        else:
            # Partial line, print with color and without reset, which will be
            # handled by some future call
            self.logger.print(color + line)
            self._trailing_color = True

    def handle_serial_input(self, data, console_parser, coredump, gdb_helper, line_matcher,
                            check_gdb_stub_and_run, finalize_line=False):
        #  type: (bytes, ConsoleParser, CoreDump, Optional[GDBHelper], LineMatcher, Callable, bool) -> None
        # Remove "+" after Continue command
        if self.start_cmd_sent:
            self.start_cmd_sent = False
            pos = data.find(b'+')
            if pos != -1:
                data = data[(pos + 1):]

        sp = self.splitdata(data)
        if self.binary_log_detected:
            text_lines, self._last_line_part = self.binlog.convert_to_text(sp[0])
            for line in text_lines:
                self.print_colored(line)
            return

        for line in sp:
            line_strip = line.strip()
            if self._serial_check_exit and line_strip == EXIT_KEY.encode('latin-1'):
                raise SerialStopException()
            if self.target != 'linux':
                self.check_panic_decode_trigger(line_strip)
            with coredump.check(line_strip):
                try:
                    decoded_line = line.decode()
                    self.decode_error_cnt = 0
                except UnicodeDecodeError:
                    decoded_line = line_strip.decode(errors='ignore')
                    self.decode_error_cnt += 1
                    if self.decode_error_cnt >= 3:
                        warning_print('Failed to decode multiple lines in a row. Try checking the baud rate and XTAL frequency setting in menuconfig')
                        self.decode_error_cnt = 0
                if self._force_line_print or line_matcher.match(decoded_line):
                    self.print_colored(line)
                    self.compare_elf_sha256(decoded_line)
                    self.logger.handle_possible_pc_address_in_line(line_strip)
            check_gdb_stub_and_run(line_strip)
            self._force_line_print = False

        if self._last_line_part.startswith(CONSOLE_STATUS_QUERY):
            self.logger.print(CONSOLE_STATUS_QUERY)
            self._last_line_part = self._last_line_part[len(CONSOLE_STATUS_QUERY):]

        # Now we have the last part (incomplete line) in _last_line_part. By
        # default we don't touch it and just wait for the arrival of the rest
        # of the line. But after some time when we didn't received it we need
        # to make a decision.
        force_print_or_matched = any((
            self._force_line_print,
            (finalize_line and line_matcher.match(self._last_line_part.decode(errors='ignore')))
        ))
        if self._last_line_part != b'' and force_print_or_matched:
            self._force_line_print = True
            self.print_colored(self._last_line_part)
            self.logger.handle_possible_pc_address_in_line(self._last_line_part, insert_new_line=True)
            check_gdb_stub_and_run(self._last_line_part)
            # It is possible that the incomplete line cuts in half the PC
            # address. A small buffer is kept and will be used the next time
            # handle_possible_pc_address_in_line is invoked to avoid this problem.
            # ADDRESS_RE matches 10 character long addresses. Therefore, we
            # keep the last 9 characters.
            self.logger.pc_address_buffer = self._last_line_part[-9:]
            # GDB sequence can be cut in half also. GDB sequence is 7
            # characters long, therefore, we save the last 6 characters.
            if gdb_helper:
                gdb_helper.gdb_buffer = self._last_line_part[-6:]
            self._last_line_part = b''
        # else: keeping _last_line_part and it will be processed the next time
        # handle_serial_input is invoked

    def check_panic_decode_trigger(self, line):  # type: (bytes) -> None
        if self._decode_panic == PANIC_DECODE_DISABLE:
            return

        if self._reading_panic == PANIC_IDLE and re.search(PANIC_START, line.decode('ascii', errors='ignore')):
            self._reading_panic = PANIC_READING
            note_print('Stack dump detected')

        if self._reading_panic == PANIC_READING and PANIC_STACK_DUMP in line:
            self._reading_panic = PANIC_READING_STACK
            self.logger.output_enabled = False

        if self._reading_panic in [PANIC_READING, PANIC_READING_STACK]:
            self._panic_buffer += line.replace(b'\r', b'') + b'\n'

        if self._reading_panic == PANIC_READING_STACK and not line:
            self._reading_panic = PANIC_IDLE
            self.logger.output_enabled = True
            try:
                out = self.panic_handler.process_panic_output(self._panic_buffer)
                if out:
                    note_print('Backtrace:\n\n', prefix='\n')
                    self.logger.print(out)
            except subprocess.CalledProcessError as e:
                warning_print(f'Failed to run gdb_panic_server.py script: {e}\n{e.output}\n\n')
                # in case of error, print the rest of panic buffer that wasn't logged yet
                # we stopped logging with PANIC_STACK_DUMP and re-enabled logging with PANIC_END
                l_idx = self._panic_buffer.find(PANIC_STACK_DUMP)
                r_idx = self._panic_buffer.find(PANIC_END)
                self.logger.print(self._panic_buffer[l_idx:r_idx])

            self._panic_buffer = b''

    def get_flashed_sha256(self, line: str) -> Optional[str]:
        elf_sha256_matcher = re.compile(
            r'ELF file SHA256:\s+(?P<sha256_flashed>[a-z0-9]+)'
        )
        file_sha256_flashed_match = re.search(elf_sha256_matcher, line)
        if not file_sha256_flashed_match:
            return None
        return file_sha256_flashed_match.group('sha256_flashed')

    def compare_elf_sha256(self, line):  # type: (str) -> None
        file_sha256_flashed = self.get_flashed_sha256(line)
        if not file_sha256_flashed:
            return
        if not all([os.path.exists(file) for file in self.elf_files]):
            warning_print('ELF file not found. '
                          "You need to build & flash the project before running 'monitor', "
                          'and the binary on the device must match the one in the build directory exactly. ')
        else:
            for elf_file in self.elf_files:
                file_sha256_build = get_sha256(elf_file)
                if file_sha256_flashed in f'{file_sha256_build}':
                    break
            else:
                warning_print(f'Checksum mismatch between flashed and built applications. '
                              f'Checksum of built application is {file_sha256_build}')

    def handle_commands(self, cmd, chip, run_make_func, console_reader, serial_reader):
        # type: (int, str, Callable, ConsoleReader, Reader) -> None

        if chip == 'linux':
            if cmd in [CMD_RESET,
                       CMD_MAKE,
                       CMD_APP_FLASH,
                       CMD_ENTER_BOOT]:
                warning_print('Linux target does not support this command')
                return

        if cmd == CMD_STOP:
            console_reader.stop()
            serial_reader.stop()
        elif cmd == CMD_RESET:
            self.reset.hard()
            self.logger.output_enabled = True
        elif cmd == CMD_MAKE:
            run_make_func('encrypted-flash' if self.encrypted else 'flash')
        elif cmd == CMD_APP_FLASH:
            run_make_func('encrypted-app-flash' if self.encrypted else 'app-flash')
        elif cmd == CMD_OUTPUT_TOGGLE:
            self.logger.output_toggle()
        elif cmd == CMD_TOGGLE_LOGGING:
            self.logger.toggle_logging()
        elif cmd == CMD_TOGGLE_TIMESTAMPS:
            self.logger.toggle_timestamps()
        elif cmd == CMD_ENTER_BOOT:
            note_print(f'Pause app (enter bootloader mode), press {key_description(MENU_KEY)} {key_description(CHIP_RESET_KEY)} to restart')
            self.reset.to_bootloader()
        else:
            raise RuntimeError('Bad command data %d' % cmd)  # type: ignore


class SerialHandlerNoElf(SerialHandler):
    # This method avoids using 'gdb_helper,' 'coredump' and 'handle_possible_pc_address_in_line'
    # where the elf file is required to be passed
    def handle_serial_input(self, data, console_parser, coredump, gdb_helper, line_matcher,
                            check_gdb_stub_and_run, finalize_line=False):
        #  type: (bytes, ConsoleParser, CoreDump, Optional[GDBHelper], LineMatcher, Callable, bool) -> None

        if self.start_cmd_sent:
            self.start_cmd_sent = False
            pos = data.find(b'+')
            if pos != -1:
                data = data[(pos + 1):]

        sp = self.splitdata(data)
        for line in sp:
            if self._serial_check_exit and line.strip() == EXIT_KEY.encode('latin-1'):
                raise SerialStopException()

            if self._force_line_print or line_matcher.match(line.decode(errors='ignore')):
                self.print_colored(line)
                self._force_line_print = False

        if self._last_line_part.startswith(CONSOLE_STATUS_QUERY):
            self.logger.print(CONSOLE_STATUS_QUERY)
            self._last_line_part = self._last_line_part[len(CONSOLE_STATUS_QUERY):]

        force_print_or_matched = any((
            self._force_line_print,
            (finalize_line and line_matcher.match(self._last_line_part.decode(errors='ignore')))
        ))

        if self._last_line_part != b'' and force_print_or_matched:
            self._force_line_print = True
            self.print_colored(self._last_line_part)
            self._last_line_part = b''
