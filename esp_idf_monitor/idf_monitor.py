#!/usr/bin/env python
#
# esp-idf serial output monitor tool. Does some helpful things:
# - Looks up hex addresses in ELF file with addr2line
# - Reset ESP32 via serial RTS line (Ctrl-T Ctrl-R)
# - Run flash build target to rebuild and flash entire project (Ctrl-T Ctrl-F; fast reflash by default)
# - Run app-flash build target to rebuild and flash app only (Ctrl-T Ctrl-A)
# - Run full flash of the project, disabling fast reflash (Ctrl-T Ctrl-E)
# - If gdbstub output is detected, gdb is automatically loaded
# - If core dump output is detected, it is converted to a human-readable report
#   by espcoredump.py.
#
# SPDX-FileCopyrightText: 2015-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#
# Contains elements taken from miniterm "Very simple serial terminal" which
# is part of pySerial. https://github.com/pyserial/pyserial
# (C)2002-2015 Chris Liechti <cliechti@gmx.net>
#
# Originally released under BSD-3-Clause license.
#

import codecs
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from types import FrameType  # noqa: F401
from typing import Callable  # noqa: F401
from typing import Dict  # noqa: F401
from typing import List  # noqa: F401
from typing import NoReturn  # noqa: F401
from typing import Optional  # noqa: F401
from typing import Tuple  # noqa: F401
from typing import Type  # noqa: F401
from typing import Union  # noqa: F401

import serial
from esp_pylib.errors import NoSerialPortFoundError
from esp_pylib.excepthook import install_exception_reporting
from esp_pylib.logger import log
from esp_pylib.rom import get_rom_elf_path
from esp_pylib.serial_ports import detect_port as _pylib_detect_port
from esp_pylib.serial_ports import get_port_names
from serial.tools import miniterm

from esp_idf_monitor import __version__

# Windows console stuff
from esp_idf_monitor.base.ansi_color_converter import get_ansi_converter
from esp_idf_monitor.base.argument_parser import invoke as _cli_invoke
from esp_idf_monitor.base.command_reader import CommandReader
from esp_idf_monitor.base.console_parser import ConsoleParser
from esp_idf_monitor.base.console_reader import ConsoleReader
from esp_idf_monitor.base.constants import CTRL_C
from esp_idf_monitor.base.constants import CTRL_H
from esp_idf_monitor.base.constants import DEFAULT_PRINT_FILTER
from esp_idf_monitor.base.constants import DEFAULT_TARGET_RESET
from esp_idf_monitor.base.constants import DEFAULT_TOOLCHAIN_PREFIX
from esp_idf_monitor.base.constants import ESPPORT_ENVIRON
from esp_idf_monitor.base.constants import ESPTOOL_OPEN_PORT_ATTEMPTS_ENVIRON
from esp_idf_monitor.base.constants import EVENT_QUEUE_TIMEOUT
from esp_idf_monitor.base.constants import GDB_EXIT_TIMEOUT
from esp_idf_monitor.base.constants import GDB_UART_CONTINUE_COMMAND
from esp_idf_monitor.base.constants import LAST_LINE_THREAD_INTERVAL
from esp_idf_monitor.base.constants import MAKEFLAGS_ENVIRON
from esp_idf_monitor.base.constants import PANIC_DECODE_DISABLE
from esp_idf_monitor.base.constants import PANIC_IDLE
from esp_idf_monitor.base.constants import TAG_CMD
from esp_idf_monitor.base.constants import TAG_KEY
from esp_idf_monitor.base.constants import TAG_SERIAL
from esp_idf_monitor.base.constants import TAG_SERIAL_FLUSH
from esp_idf_monitor.base.coredump import COREDUMP_DECODE_INFO
from esp_idf_monitor.base.coredump import CoreDump
from esp_idf_monitor.base.gdbhelper import GDBHelper
from esp_idf_monitor.base.key_config import EXIT_KEY
from esp_idf_monitor.base.key_config import EXIT_MENU_KEY
from esp_idf_monitor.base.key_config import MENU_KEY
from esp_idf_monitor.base.line_matcher import LineMatcher
from esp_idf_monitor.base.logger import Logger
from esp_idf_monitor.base.monitor_log import install_monitor_log
from esp_idf_monitor.base.serial_handler import SerialHandler
from esp_idf_monitor.base.serial_handler import SerialHandlerNoElf
from esp_idf_monitor.base.serial_handler import run_make
from esp_idf_monitor.base.serial_reader import LinuxReader  # noqa: F401
from esp_idf_monitor.base.serial_reader import Reader  # noqa: F401
from esp_idf_monitor.base.serial_reader import SerialReader  # noqa: F401
from esp_idf_monitor.base.stoppable_thread import StoppableThread  # noqa: F401
from esp_idf_monitor.base.web_socket_client import WebSocketClient
from esp_idf_monitor.config import Config

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
        non_interactive=False,  # type: bool
        port_candidates=None,  # type: Optional[List[str]]
    ):
        self.event_queue = queue.Queue()  # type: queue.Queue
        self.cmd_queue = queue.Queue()  # type: queue.Queue
        self.non_interactive = non_interactive
        # ConsoleBase writes to stdout but never touches the TTY, so it is safe
        # to use when stdin is not attached to a terminal (pipe, file, CI).
        # The Console subclass requires a real TTY on construction.
        self.console = miniterm.ConsoleBase() if non_interactive else miniterm.Console()
        # Chip/serial ANSI (stdout via miniterm) still needs the Windows converter.
        # Monitor messages go through Rich on the real sys.stderr — do not wrap it,
        # or EspLog.err/warn lose TTY detection (no color, hard-wrap at 80 cols).
        self.console.output = get_ansi_converter(self.console.output, force_color=force_color)
        self.console.byte_output = get_ansi_converter(self.console.byte_output, force_color=force_color)

        self.elf_files = elf_files or []
        self.elf_exists = self._check_elfs()
        self.logger = Logger(
            self.elf_files,
            self.console,
            timestamps,
            timestamp_format,
            enable_address_decoding,
            toolchain_prefix,
            rom_elf_file=rom_elf_file,
        )

        self.coredump = (
            CoreDump(decode_coredumps, self.event_queue, self.logger, websocket_client, self.elf_files)
            if self.elf_exists
            else None
        )

        # allow for possibility the "make" arg is a list of arguments (for idf.py)
        self.make = make if os.path.exists(make) else shlex.split(make)  # type: Union[str, List[str]]
        self.target = target
        self.timeout_cnt = 0

        if isinstance(self, SerialMonitor):
            self.serial = serial_instance
            self.serial_reader = SerialReader(
                self.serial, self.event_queue, reset, open_port_attempts, target, port_candidates
            )  # type: Reader

            self.gdb_helper = (
                GDBHelper(toolchain_prefix, websocket_client, self.elf_files, self.serial.port, self.serial.baudrate)
                if self.elf_exists
                else None
            )

        else:
            if len(self.elf_files) > 1:
                log.warn(
                    f'Found {len(self.elf_files)} ELF files, but Linux target only supports one. '
                    f'Using: {self.elf_files[0]}'
                )

            if not os.path.exists(self.elf_files[0]):
                log.die(
                    f'ELF file {self.elf_files[0]} does not exist, cannot run monitor on Linux target. '
                    'Please build the project first.'
                )

            self.serial = subprocess.Popen(
                self.elf_files[0], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0
            )
            self.serial_reader = LinuxReader(self.serial, self.event_queue)

            self.gdb_helper = None

        self.console_parser = ConsoleParser(eol)
        if non_interactive:
            # Without a TTY on stdin, keys cannot be read; read line-based
            # commands from stdin instead, so the monitor can be scripted.
            command_reader = CommandReader(self.event_queue, self.console_parser)
            self.console_reader = command_reader  # type: StoppableThread
            # feed every decoded serial line to the reader for the 'expect' command
            line_observer = command_reader.observe_line  # type: Optional[Callable[[str], None]]
        else:
            self.console_reader = ConsoleReader(self.console, self.event_queue, self.cmd_queue, self.console_parser)
            line_observer = None

        cls = SerialHandler if self.elf_exists else SerialHandlerNoElf
        self.serial_handler = cls(
            b'',
            self.logger,
            decode_panic,
            PANIC_IDLE,
            b'',
            target,
            False,
            False,
            self.serial,
            encrypted,
            self.elf_files,
            toolchain_prefix,
            disable_auto_color,
            line_observer=line_observer,
        )

        self._line_matcher = LineMatcher(print_filter)

        # internal state
        self._invoke_processing_last_line_timer = None  # type: Optional[threading.Timer]
        self._gdb_stub_warned = False

    def __enter__(self) -> None:
        """Use 'with self' to temporarily disable monitoring behaviour"""
        self.serial_reader.stop()
        if not self.non_interactive:
            # The CommandReader is left running: it cannot be restarted while
            # it is blocked in readline() and it does not touch the TTY, so
            # there is no need to suspend it. Commands read meanwhile are
            # queued and executed once the main loop resumes.
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
                log.warn(f"ELF file '{elf_file}' does not exist")
        return exists

    def run_make(self, target: str, env_extra: Optional[Dict[str, str]] = None) -> None:
        # Run a build-system target (e.g. flash / app-flash). env_extra adds
        # variables to the subprocess environment, letting callers influence the
        # build without target-specific parameters here (e.g. IDF_FLASH_FULL=1
        # for a full flash).
        with self:
            run_make(
                target,
                self.make,
                self.console,
                self.console_parser,
                self.event_queue,
                self.cmd_queue,
                self.logger,
                non_interactive=self.non_interactive,
                env_extra=env_extra,
            )

    def _pre_start(self) -> None:
        self.console_reader.start()
        self.serial_reader.start()

    def main_loop(self) -> None:
        if self.non_interactive:
            # SIGTERM is how Docker/CI stop processes; reuse the Ctrl+C
            # (KeyboardInterrupt) clean-shutdown path so the log file is
            # flushed and closed. The default action would kill the process
            # without running any cleanup.
            def _sigterm_handler(signum: int, frame: Optional[FrameType]) -> None:
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, _sigterm_handler)
        self._pre_start()

        try:
            while self.console_reader.alive and self.serial_reader.alive:
                try:
                    self._main_loop()
                except KeyboardInterrupt:
                    if self.non_interactive:
                        # there is no exit key without a TTY, Ctrl+C exits
                        break
                    log.note(
                        f'To exit from IDF monitor please use "{key_description(EXIT_KEY)}". Alternatively, '
                        f'you can use {key_description(MENU_KEY)} {key_description(EXIT_MENU_KEY)} to exit.'
                    )
                    self.serial_write(codecs.encode(CTRL_C))
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
            log.print('')  # newline

    def serial_write(self, *args: bytes, **kwargs: str) -> None:
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
            if isinstance(self.serial_reader, SerialReader):
                self.serial_handler.reset = self.serial_reader.reset_strategy
            self.serial_handler.handle_commands(
                data, self.target, self.run_make, self.console_reader, self.serial_reader
            )
        elif event_tag == TAG_KEY:
            # stdin may contain invalid UTF-8; Python exposes those as
            # surrogateescape code points (e.g. b'\xe3' -> '\udce3'). Encode
            # with the same error handler so the original byte is preserved.
            self.serial_write(codecs.encode(data, 'utf-8', 'surrogateescape'))
        elif event_tag == TAG_SERIAL:
            self.serial_handler.handle_serial_input(
                data,
                self.console_parser,
                self.coredump,  # type: ignore
                self.gdb_helper,
                self._line_matcher,
                self.check_gdb_stub_and_run,
            )
            if self._invoke_processing_last_line_timer is not None:
                self._invoke_processing_last_line_timer.cancel()
            self._invoke_processing_last_line_timer = threading.Timer(
                LAST_LINE_THREAD_INTERVAL, self.invoke_processing_last_line
            )
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
            self.serial_handler.handle_serial_input(
                data,
                self.console_parser,
                self.coredump,  # type: ignore
                self.gdb_helper,
                self._line_matcher,
                self.check_gdb_stub_and_run,
                finalize_line=not self.coredump or not self.coredump.in_progress,
            )
        else:
            raise RuntimeError(f'Bad event data {((event_tag, data),)}')


class SerialMonitor(Monitor):
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        """Use 'with self' to temporarily disable monitoring behaviour"""
        if not self.non_interactive:
            # the CommandReader was not stopped in __enter__
            self.console_reader.start()
        if self.elf_exists:
            self.serial_reader.gdb_exit = self.gdb_helper.gdb_exit  # type: ignore # write gdb_exit flag
        self.serial_reader.start()

    def _pre_start(self) -> None:
        super()._pre_start()
        if self.elf_exists:
            self.gdb_helper.gdb_exit = False  # type: ignore
        self.serial_handler.start_cmd_sent = False

    def serial_write(self, *args: bytes, **kwargs: str) -> None:
        self.serial: serial.Serial
        try:
            self.serial.write(*args, **kwargs)
            self.timeout_cnt = 0
        except serial.SerialTimeoutException:
            if not self.timeout_cnt:
                log.warn(
                    'Writing to serial is timing out. Please make sure that your application supports '
                    'an interactive console and that you have picked the correct console for serial communication.'
                )
            self.timeout_cnt += 1
            self.timeout_cnt %= 3
        except serial.SerialException:
            pass  # this shouldn't happen, but sometimes port has closed in serial thread
        except UnicodeEncodeError:
            pass  # this can happen if a non-ascii character was passed, ignoring

    def check_gdb_stub_and_run(self, line: bytes) -> None:  # type: ignore # The base class one is a None value
        if self.gdb_helper:
            # Autodetection may have selected a fallback after this helper was constructed.
            self.gdb_helper.port = self.serial.port
        if self.gdb_helper and self.gdb_helper.check_gdb_stub_trigger(line):
            if self.non_interactive:
                # gdb would read from the same stdin as the CommandReader and
                # there is no terminal for an interactive session anyway
                if not self._gdb_stub_warned:
                    log.warn(
                        'GDB stub detected, but an interactive GDB session cannot be started in non-interactive mode'
                    )
                    self._gdb_stub_warned = True
                return
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
        """Use 'with self' to temporarily disable monitoring behaviour"""
        if not self.non_interactive:
            # the CommandReader was not stopped in __enter__
            self.console_reader.start()
        self.serial_reader.start()

    def serial_write(self, *args: bytes, **kwargs: str) -> None:
        self.serial.stdin.write(*args, **kwargs)
        self.serial.stdin.flush()

    def check_gdb_stub_and_run(self, line: bytes) -> None:
        return  # fake function for linux target


def detect_port() -> Union[str, NoReturn]:
    """Detect connected ports and return the highest-priority one."""
    try:
        port = _pylib_detect_port()
    except NoSerialPortFoundError:
        log.die('No serial ports detected.')

    log.print(f'Using autodetected port {port}', style='yellow')
    return port


def _run_monitor(
    *,
    port: Optional[str],
    no_reset: bool,
    disable_address_decoding: bool,
    baud: int,
    make: str,
    encrypted: bool,
    toolchain_prefix: str,
    eol: Optional[str],
    rom_elf_file: Optional[str],
    print_filter: str,
    decode_coredumps: str,
    decode_panic: str,
    target: str,
    revision: int,
    ws: Optional[str],
    timestamps: bool,
    timestamp_format: str,
    force_color: bool,
    disable_auto_color: bool,
    open_port_attempts: int,
    save_log: bool,
    elf_files: Tuple[str, ...],
) -> None:
    """Run the monitor with the parsed CLI parameters.

    Factored out of `main` so the same execution path can be
    invoked from the click command (production) and from tests that want
    to drive the monitor without going through ``cli.main(argv)``.
    """
    # Without a TTY on stdin (pipe, file, CI) interactive key reading is not
    # possible; switch to the non-interactive mode, where line-based commands
    # are read from stdin instead (see CommandReader).
    non_interactive = not sys.stdin.isatty()

    # Default EOL: LF for Linux targets, CR otherwise. Matches the
    # historical behaviour the existing host tests pin.
    resolved_eol = eol or ('LF' if target == 'linux' else 'CR')

    if rom_elf_file is None:
        resolved_rom_elf = get_rom_elf_path(target, revision)
    else:
        resolved_rom_elf = rom_elf_file

    # Remove the parallel jobserver arguments from MAKEFLAGS, as any
    # parent make is only running 1 job (monitor), so we can re-spawn
    # all of the child makes we need (the -j argument remains part of
    # MAKEFLAGS).
    try:
        makeflags = os.environ[MAKEFLAGS_ENVIRON]
        makeflags = re.sub(r'--jobserver[^ =]*=[0-9,]+ ?', '', makeflags)
        os.environ[MAKEFLAGS_ENVIRON] = makeflags
    except KeyError:
        pass  # not running a make jobserver

    # Pin the IDE WebSocket URL on the shared esp_pylib.ws module *before*
    # constructing the monitor's WebSocketClient. This means `log.warn` /
    # `log.err` calls forward their structured diagnostics to the same URL as
    # the gdb / coredump event channel without each call site having to know about the URL.
    if ws:
        try:
            from esp_pylib.ws import set_ws_url as _set_ide_ws_url

            _set_ide_ws_url(ws)
        except ImportError:
            # Python 3.7 path: esp-pylib's [ide] extra isn't installed, so
            # only the legacy WebSocketClient backend will speak to the
            # IDE.
            pass

    ws_client = WebSocketClient(ws) if ws else None

    elf_files_list = list(elf_files)
    port_candidates = None  # type: Optional[List[str]]

    try:
        cls: Type[Monitor]
        if target == 'linux':
            serial_instance = None
            cls = LinuxMonitor
            log.print(f'esp-idf-monitor {__version__} on linux', style='yellow')
        else:
            # Use a local variable so any in-flight port-name fixups don't
            # leak back into ``port`` (which is also passed to the env-var
            # propagation below verbatim, matching the legacy contract that
            # downstream tools like idf.py inspect ``ESPPORT``).
            active_port = port

            # If no port was given, detect connected ports and use one of them.
            if active_port is None:
                active_port = detect_port()
                port_candidates = [active_port]
                port_candidates.extend(candidate for candidate in get_port_names() if candidate != active_port)
            # GDB uses CreateFile to open COM port, which requires the COM name
            # to be r'\\.\COMx' if the COM number is larger than 10.
            if os.name == 'nt' and active_port.startswith('COM'):
                active_port = active_port.replace('COM', r'\\.\COM')
                log.warn('GDB cannot open serial ports accessed as COMx')
                log.note(f'Using {active_port} instead...')
            elif active_port.startswith('/dev/tty.') and sys.platform == 'darwin':
                active_port = active_port.replace('/dev/tty.', '/dev/cu.')
                log.warn('Serial ports accessed as /dev/tty.* will hang gdb if launched.')
                log.note(f'Using {active_port} instead...')

            if port_candidates is not None:
                normalized_candidates = []
                for candidate in port_candidates:
                    if os.name == 'nt' and candidate.startswith('COM'):
                        candidate = candidate.replace('COM', r'\\.\COM')
                    elif sys.platform == 'darwin' and candidate.startswith('/dev/tty.'):
                        candidate = candidate.replace('/dev/tty.', '/dev/cu.')
                    if candidate not in normalized_candidates:
                        normalized_candidates.append(candidate)
                port_candidates = normalized_candidates

            serial_instance = serial.serial_for_url(active_port, baud, do_not_open=True, exclusive=True)
            # setting write timeout is not supported for RFC2217 in pyserial
            if not active_port.startswith('rfc2217://'):
                serial_instance.write_timeout = 0.3

            # Pass the actual used port to callee of idf_monitor (e.g.
            # idf.py / cmake) through the ``ESPPORT`` environment
            # variable. Note that the env var must carry the ORIGINAL
            # ``port`` argument without any in-flight fixups (idf.py has
            # a check for this).
            espport_val = str(port)
            os.environ.update(
                {ESPPORT_ENVIRON: espport_val, ESPTOOL_OPEN_PORT_ATTEMPTS_ENVIRON: str(open_port_attempts)}
            )

            cls = SerialMonitor
            log.print(
                f'esp-idf-monitor {__version__} on {serial_instance.name} {serial_instance.baudrate}',
                style='yellow',
            )

        monitor = cls(
            serial_instance,
            elf_files_list,
            print_filter,
            make,
            encrypted,
            not no_reset,
            open_port_attempts,
            toolchain_prefix,
            resolved_eol,
            decode_coredumps,
            decode_panic,
            target,
            ws_client,
            not disable_address_decoding,
            timestamps,
            timestamp_format,
            force_color,
            disable_auto_color,
            resolved_rom_elf,
            non_interactive,
            port_candidates,
        )

        if save_log:
            monitor.logger.start_logging()

        if non_interactive:
            extras = ['send <text>', 'sleep <seconds>', 'expect <regex>', 'exit']
            log.print(
                'Standard input is not a TTY, running in non-interactive mode. Reading commands '
                f'from stdin: {", ".join(list(CommandReader.COMMANDS) + extras)} '
                "| Quit: 'exit' command, EOF or Ctrl+C",
                style='yellow',
            )
        else:
            log.print(
                'Quit: {q} | Menu: {m} | Help: {m} followed by {h}'.format(
                    q=key_description(EXIT_KEY), m=key_description(MENU_KEY), h=key_description(CTRL_H)
                ),
                style='yellow',
            )

        if print_filter != DEFAULT_PRINT_FILTER:
            msg = ''
            # Check if environment variable was used to set print_filter
            if print_filter == os.environ.get('ESP_IDF_MONITOR_PRINT_FILTER', None):
                msg = ' (set with ESP_IDF_MONITOR_PRINT_FILTER environment variable)'
            log.print(f'Print filter: "{print_filter}"{msg}', style='yellow')
        Config().load_configuration(verbose=True)
        monitor.main_loop()
    except KeyboardInterrupt:
        pass
    finally:
        if ws_client:
            ws_client.close()


def main() -> None:
    """CLI entry point invoked by the `idf-monitor` console script.

    Wires the rich-click command in `argument_parser` to the
    `_run_monitor` implementation and installs the IDE exception
    reporting hook before any other work. `install_exception_reporting`
    is safe to call multiple times and chains to any pre-installed
    `sys.excepthook` / `threading.excepthook`, so a wrapping IDE
    integration that already set its own hook keeps working.

    `install_monitor_log` is called here - at the application entry point -
    rather than on package import, so merely importing `esp_idf_monitor`
    (e.g. idf.py importing `get_ansi_converter`) does not hijack the caller's
    shared esp-pylib logger with the monitor's `--- ` prefix.
    """
    install_monitor_log()
    install_exception_reporting()
    _cli_invoke(_run_monitor)
