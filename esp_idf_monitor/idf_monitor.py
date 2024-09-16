#!/usr/bin/env python
#
# esp-idf serial output monitor tool. Does some helpful things:
# - Looks up hex addresses in ELF file with addr2line
# - Reset ESP32 via serial RTS line (Ctrl-T Ctrl-R)
# - Run flash build target to rebuild and flash entire project (Ctrl-T Ctrl-F)
# - Run app-flash build target to rebuild and flash app only (Ctrl-T Ctrl-A)
# - If gdbstub output is detected, gdb is automatically loaded
# - If core dump output is detected, it is converted to a human-readable report
#   by espcoredump.py.
#
# SPDX-FileCopyrightText: 2015-2024 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
# Contains elements taken from miniterm "Very simple serial terminal" which
# is part of pySerial. https://github.com/pyserial/pyserial
# (C)2002-2015 Chris Liechti <cliechti@gmx.net>
#
# Originally released under BSD-3-Clause license.
#

import codecs
import io
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, List, NoReturn, Optional, Type, Union  # noqa: F401

import serial
from serial.tools import list_ports, miniterm

# Windows console stuff
from esp_idf_monitor.base.ansi_color_converter import get_ansi_converter
from esp_idf_monitor.base.argument_parser import get_parser
from esp_idf_monitor.base.console_parser import ConsoleParser
from esp_idf_monitor.base.console_reader import ConsoleReader
from esp_idf_monitor.base.constants import (CTRL_C, CTRL_H,
                                            DEFAULT_PRINT_FILTER,
                                            DEFAULT_TARGET_RESET,
                                            DEFAULT_TOOLCHAIN_PREFIX,
                                            ESPPORT_ENVIRON,
                                            ESPTOOL_OPEN_PORT_ATTEMPTS_ENVIRON,
                                            EVENT_QUEUE_TIMEOUT,
                                            FILTERED_PORTS, GDB_EXIT_TIMEOUT,
                                            GDB_UART_CONTINUE_COMMAND,
                                            LAST_LINE_THREAD_INTERVAL,
                                            MAKEFLAGS_ENVIRON,
                                            PANIC_DECODE_DISABLE, PANIC_IDLE,
                                            TAG_CMD, TAG_KEY, TAG_SERIAL,
                                            TAG_SERIAL_FLUSH)
from esp_idf_monitor.base.coredump import COREDUMP_DECODE_INFO, CoreDump
from esp_idf_monitor.base.exceptions import SerialStopException
from esp_idf_monitor.base.gdbhelper import GDBHelper
from esp_idf_monitor.base.key_config import EXIT_KEY, EXIT_MENU_KEY, MENU_KEY
from esp_idf_monitor.base.line_matcher import LineMatcher
from esp_idf_monitor.base.logger import Logger
from esp_idf_monitor.base.output_helpers import (normal_print, note_print,
                                                 warning_print)
from esp_idf_monitor.base.rom_elf_getter import get_rom_elf_path
from esp_idf_monitor.base.serial_handler import (SerialHandler,
                                                 SerialHandlerNoElf, run_make)
from esp_idf_monitor.base.serial_reader import (LinuxReader, Reader,
                                                SerialReader)
from esp_idf_monitor.base.web_socket_client import WebSocketClient
from esp_idf_monitor.config import Config

from . import __version__

key_description = miniterm.key_description


class Monitor:
    """
    Monitor application base class.

    This was originally derived from miniterm.Miniterm, but it turned out to be easier to write from scratch for this
    purpose.

    Main difference is that all event processing happens in the main thread, not the worker threads.
    """

    def __init__(
        self,
        serial_instance,  # type: serial.Serial
        elf_files,  # type: List[str]
        print_filter,  # type: str
        make='make',  # type: str
        encrypted=False,  # type: bool
        reset=DEFAULT_TARGET_RESET,  # type: bool
        open_port_attempts=1,  # type: int
        toolchain_prefix=DEFAULT_TOOLCHAIN_PREFIX,  # type: str
        eol='CRLF',  # type: str
        decode_coredumps=COREDUMP_DECODE_INFO,  # type: str
        decode_panic=PANIC_DECODE_DISABLE,  # type: str
        target='esp32',  # type: str
        websocket_client=None,  # type: Optional[WebSocketClient]
        enable_address_decoding=True,  # type: bool
        timestamps=False,  # type: bool
        timestamp_format='',  # type: str
        force_color=False,  # type: bool
        disable_auto_color=False,  # type: bool
        rom_elf_file=None,  # type: Optional[str]
    ):
        self.event_queue = queue.Queue()  # type: queue.Queue
        self.cmd_queue = queue.Queue()  # type: queue.Queue
        self.console = miniterm.Console()
        # if the variable is set ANSI will be printed even if we do not print to terminal
        sys.stderr = get_ansi_converter(sys.stderr, force_color=force_color)  # type: ignore
        self.console.output = get_ansi_converter(self.console.output, force_color=force_color)
        self.console.byte_output = get_ansi_converter(self.console.byte_output, force_color=force_color)

        self.elf_files = elf_files or []
        self.elf_exists = self._check_elfs()
        self.logger = Logger(self.elf_files, self.console, timestamps, timestamp_format, enable_address_decoding,
                             toolchain_prefix, rom_elf_file=rom_elf_file)

        self.coredump = CoreDump(decode_coredumps, self.event_queue, self.logger, websocket_client,
                                 self.elf_files) if self.elf_exists else None

        # allow for possibility the "make" arg is a list of arguments (for idf.py)
        self.make = make if os.path.exists(make) else shlex.split(make)  # type: Any[Union[str, List[str]], str]
        self.target = target
        self.timeout_cnt = 0

        if isinstance(self, SerialMonitor):
            # testing hook: when running tests, input from console is ignored
            socket_test_mode = os.environ.get('ESP_IDF_MONITOR_TEST') == '1'
            self.serial = serial_instance
            self.serial_reader = SerialReader(self.serial, self.event_queue, reset, open_port_attempts, target)  # type: Reader

            self.gdb_helper = GDBHelper(toolchain_prefix, websocket_client, self.elf_files, self.serial.port,
                                        self.serial.baudrate) if self.elf_exists else None

        else:
            socket_test_mode = False
            self.serial = subprocess.Popen(self.elf_files, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, bufsize=0)
            self.serial_reader = LinuxReader(self.serial, self.event_queue)

            self.gdb_helper = None

        cls = SerialHandler if self.elf_exists else SerialHandlerNoElf
        self.serial_handler = cls(b'', socket_test_mode, self.logger, decode_panic, PANIC_IDLE, b'', target,
                                  False, False, self.serial, encrypted, self.elf_files, toolchain_prefix, disable_auto_color)

        self.console_parser = ConsoleParser(eol)
        self.console_reader = ConsoleReader(self.console, self.event_queue, self.cmd_queue, self.console_parser,
                                            socket_test_mode)

        self._line_matcher = LineMatcher(print_filter)

        # internal state
        self._invoke_processing_last_line_timer = None  # type: Optional[threading.Timer]

    def __enter__(self) -> None:
        """ Use 'with self' to temporarily disable monitoring behaviour """
        self.serial_reader.stop()
        self.console_reader.stop()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        raise NotImplementedError

    def _check_elfs(self) -> bool:
        """Check if at least one file exists and print a warning if not"""
        exists = False
        for elf_file in self.elf_files:
            if os.path.exists(elf_file):
                exists = True
            else:
                warning_print(f"ELF file '{elf_file}' does not exist")
        return exists

    def run_make(self, target: str) -> None:
        with self:
            run_make(target, self.make, self.console, self.console_parser, self.event_queue, self.cmd_queue,
                     self.logger)

    def _pre_start(self) -> None:
        self.console_reader.start()
        self.serial_reader.start()

    def main_loop(self) -> None:
        self._pre_start()

        try:
            while self.console_reader.alive and self.serial_reader.alive:
                try:
                    self._main_loop()
                except KeyboardInterrupt:
                    note_print(
                        f'To exit from IDF monitor please use \"{key_description(EXIT_KEY)}\". '
                        f'Alternatively, you can use {key_description(MENU_KEY)} {key_description(EXIT_MENU_KEY)} to exit.'
                    )
                    self.serial_write(codecs.encode(CTRL_C))
        except SerialStopException:
            normal_print('Stopping condition has been received\n')
        except KeyboardInterrupt:
            pass
        finally:
            try:
                self.console_reader.stop()
                self.serial_reader.stop()
                self.logger.stop_logging()
                # Cancelling _invoke_processing_last_line_timer is not
                # important here because receiving empty data doesn't matter.
                self._invoke_processing_last_line_timer = None
            except Exception:  # noqa
                pass
            normal_print('\n')

    def serial_write(self, *args: str, **kwargs: str) -> None:
        raise NotImplementedError

    def check_gdb_stub_and_run(self, line: bytes) -> None:
        raise NotImplementedError

    def invoke_processing_last_line(self) -> None:
        self.event_queue.put((TAG_SERIAL_FLUSH, b''), False)

    def _main_loop(self) -> None:
        try:
            item = self.cmd_queue.get_nowait()
        except queue.Empty:
            try:
                item = self.event_queue.get(timeout=EVENT_QUEUE_TIMEOUT)
            except queue.Empty:
                return

        event_tag, data = item
        if event_tag == TAG_CMD:
            self.serial_handler.handle_commands(data, self.target, self.run_make, self.console_reader,
                                                self.serial_reader)
        elif event_tag == TAG_KEY:
            self.serial_write(codecs.encode(data))
        elif event_tag == TAG_SERIAL:
            self.serial_handler.handle_serial_input(data, self.console_parser, self.coredump,  # type: ignore
                                                    self.gdb_helper, self._line_matcher,
                                                    self.check_gdb_stub_and_run)
            if self._invoke_processing_last_line_timer is not None:
                self._invoke_processing_last_line_timer.cancel()
            self._invoke_processing_last_line_timer = threading.Timer(LAST_LINE_THREAD_INTERVAL,
                                                                      self.invoke_processing_last_line)
            self._invoke_processing_last_line_timer.start()
            # If no further data is received in the next short period
            # of time then the _invoke_processing_last_line_timer
            # generates an event which will result in the finishing of
            # the last line. This is fix for handling lines sent
            # without EOL.
            # finalizing the line when coredump is in progress causes decoding issues
            # the espcoredump loader uses empty line as a sign for end-of-coredump
            # line is finalized only for non coredump data
        elif event_tag == TAG_SERIAL_FLUSH:
            self.serial_handler.handle_serial_input(data, self.console_parser, self.coredump,  # type: ignore
                                                    self.gdb_helper, self._line_matcher,
                                                    self.check_gdb_stub_and_run,
                                                    finalize_line=not self.coredump or not self.coredump.in_progress)
        else:
            raise RuntimeError('Bad event data %r' % ((event_tag, data),))


class SerialMonitor(Monitor):
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        """ Use 'with self' to temporarily disable monitoring behaviour """
        self.console_reader.start()
        if self.elf_exists:
            self.serial_reader.gdb_exit = self.gdb_helper.gdb_exit  # type: ignore # write gdb_exit flag
        self.serial_reader.start()

    def _pre_start(self) -> None:
        super()._pre_start()
        if self.elf_exists:
            self.gdb_helper.gdb_exit = False  # type: ignore
        self.serial_handler.start_cmd_sent = False

    def serial_write(self, *args: str, **kwargs: str) -> None:
        self.serial: serial.Serial
        try:
            self.serial.write(*args, **kwargs)
            self.timeout_cnt = 0
        except serial.SerialTimeoutException:
            if not self.timeout_cnt:
                warning_print('Writing to serial is timing out. Please make sure that your application supports '
                              'an interactive console and that you have picked the correct console for serial communication.')
            self.timeout_cnt += 1
            self.timeout_cnt %= 3
        except serial.SerialException:
            pass  # this shouldn't happen, but sometimes port has closed in serial thread
        except UnicodeEncodeError:
            pass  # this can happen if a non-ascii character was passed, ignoring

    def check_gdb_stub_and_run(self, line: bytes) -> None:  # type: ignore # The base class one is a None value
        if self.gdb_helper and self.gdb_helper.check_gdb_stub_trigger(line):
            with self:  # disable console control
                self.gdb_helper.run_gdb()

    def _main_loop(self) -> None:
        if self.elf_exists and self.gdb_helper.gdb_exit:  # type: ignore
            self.gdb_helper.gdb_exit = False  # type: ignore
            time.sleep(GDB_EXIT_TIMEOUT)
            # Continue the program after exit from the GDB
            self.serial_write(codecs.encode(GDB_UART_CONTINUE_COMMAND))
            self.serial_handler.start_cmd_sent = True

        super()._main_loop()


class LinuxMonitor(Monitor):
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        """ Use 'with self' to temporarily disable monitoring behaviour """
        self.console_reader.start()
        self.serial_reader.start()

    def serial_write(self, *args: str, **kwargs: str) -> None:
        self.serial.stdin.write(*args, **kwargs)
        self.serial.stdin.flush()

    def check_gdb_stub_and_run(self, line: bytes) -> None:
        return  # fake function for linux target


def detect_port() -> Union[str, NoReturn]:
    """Detect connected ports and return the last one"""
    try:
        port_list = list_ports.comports()
        if sys.platform == 'darwin':
            port_list = [
                port
                for port in port_list
                if not port.device.endswith(FILTERED_PORTS)
            ]
        port: str = port_list[-1].device
        # keep the `/dev/ttyUSB0` default port on linux if connected
        # TODO: This can be removed in next major release
        if sys.platform == 'linux':
            for p in port_list:
                if p.device == '/dev/ttyUSB0':
                    port = p.device
                    break
        note_print(f'Using autodetected port {port}')
        return port
    except IndexError:
        sys.exit('No serial ports detected.')


def main() -> None:
    if not sys.stdin.isatty() and not os.environ.get('ESP_IDF_MONITOR_TEST'):
        sys.exit('Error: Monitor requires standard input to be attached to TTY. Try using a different terminal.')
    parser = get_parser()
    args = parser.parse_args()

    # use EOL from argument; defaults to LF for Linux targets and CR otherwise
    args.eol = args.eol or ('LF' if args.target == 'linux' else 'CR')

    if isinstance(args.rom_elf_file, io.BufferedReader):
        rom_elf_file = args.rom_elf_file.name
        args.rom_elf_file.close()  # don't need this as a file
    else:
        rom_elf_file = args.rom_elf_file if args.rom_elf_file is not None else get_rom_elf_path(args.target, args.revision)

    # remove the parallel jobserver arguments from MAKEFLAGS, as any
    # parent make is only running 1 job (monitor), so we can re-spawn
    # all of the child makes we need (the -j argument remains part of
    # MAKEFLAGS)
    try:
        makeflags = os.environ[MAKEFLAGS_ENVIRON]
        makeflags = re.sub(r'--jobserver[^ =]*=[0-9,]+ ?', '', makeflags)
        os.environ[MAKEFLAGS_ENVIRON] = makeflags
    except KeyError:
        pass  # not running a make jobserver

    ws = WebSocketClient(args.ws) if args.ws else None
    try:
        cls: Type[Monitor]
        if args.target == 'linux':
            serial_instance = None
            cls = LinuxMonitor
            note_print(f'esp-idf-monitor {__version__} on linux')
        else:
            # The port name is changed in cases described in the following lines.
            # Use a local argument and avoid the modification of args.port.
            port = args.port

            # if no port was set, detect connected ports and use one of them
            if port is None:
                port = detect_port()
            # GDB uses CreateFile to open COM port, which requires the COM name to be r'\\.\COMx' if the COM
            # number is larger than 10
            if os.name == 'nt' and port.startswith('COM'):
                port = port.replace('COM', r'\\.\COM')
                warning_print('GDB cannot open serial ports accessed as COMx')
                note_print(f'Using {port} instead...')
            elif port.startswith('/dev/tty.') and sys.platform == 'darwin':
                port = port.replace('/dev/tty.', '/dev/cu.')
                warning_print('Serial ports accessed as /dev/tty.* will hang gdb if launched.')
                note_print(f'Using {port} instead...')

            serial_instance = serial.serial_for_url(port, args.baud, do_not_open=True, exclusive=True)
            # setting write timeout is not supported for RFC2217 in pyserial
            if not port.startswith('rfc2217://'):
                serial_instance.write_timeout = 0.3

            # Pass the actual used port to callee of idf_monitor (e.g. idf.py/cmake) through `ESPPORT` environment
            # variable.
            # Note that the port must be original port argument without any replacement done in IDF Monitor (idf.py
            # has a check for this).
            # To make sure the key as well as the value are str type, by the requirements of subprocess
            espport_val = str(args.port)
            os.environ.update({ESPPORT_ENVIRON: espport_val, ESPTOOL_OPEN_PORT_ATTEMPTS_ENVIRON: str(args.open_port_attempts)})

            cls = SerialMonitor
            note_print('esp-idf-monitor {v} on {p.name} {p.baudrate}'.format(v=__version__, p=serial_instance))

        monitor = cls(serial_instance,
                      args.elf_files,
                      args.print_filter,
                      args.make,
                      args.encrypted,
                      not args.no_reset,
                      args.open_port_attempts,
                      args.toolchain_prefix,
                      args.eol,
                      args.decode_coredumps,
                      args.decode_panic,
                      args.target,
                      ws,
                      not args.disable_address_decoding,
                      args.timestamps,
                      args.timestamp_format,
                      args.force_color,
                      args.disable_auto_color,
                      rom_elf_file)

        note_print('Quit: {q} | Menu: {m} | Help: {m} followed by {h}'.format(
            q=key_description(EXIT_KEY),
            m=key_description(MENU_KEY),
            h=key_description(CTRL_H)))
        if args.print_filter != DEFAULT_PRINT_FILTER:
            msg = ''
            # Check if environment variable was used to set print_filter
            if args.print_filter == os.environ.get('ESP_IDF_MONITOR_PRINT_FILTER', None):
                msg = ' (set with ESP_IDF_MONITOR_PRINT_FILTER environment variable)'
            note_print(f'Print filter: "{args.print_filter}"{msg}')
        Config().load_configuration(verbose=True)
        monitor.main_loop()
    except KeyboardInterrupt:
        pass
    finally:
        if ws:
            ws.close()
