# SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
import io
import os
import queue  # noqa: F401
import tempfile
from contextlib import contextmanager, redirect_stdout
from typing import Generator, List, Optional  # noqa: F401

from .constants import TAG_KEY
from .logger import Logger  # noqa: F401
from .output_helpers import error_print, note_print, warning_print
from .web_socket_client import WebSocketClient  # noqa: F401

# coredump related messages
COREDUMP_UART_START = b'================= CORE DUMP START ================='
COREDUMP_UART_END = b'================= CORE DUMP END ================='
COREDUMP_UART_PROMPT = b'Press Enter to print core dump to UART...'

# coredump states
COREDUMP_IDLE = 0
COREDUMP_READING = 1
COREDUMP_DONE = 2

# coredump decoding options
COREDUMP_DECODE_DISABLE = 'disable'
COREDUMP_DECODE_INFO = 'info'


class CoreDump:
    def __init__(self, decode_coredumps, event_queue, logger, websocket_client, elf_files):
        # type: (str, queue.Queue, Logger, Optional[WebSocketClient], List[str]) -> None

        self._coredump_buffer = b''
        self._decode_coredumps = decode_coredumps
        self.event_queue = event_queue
        self._reading_coredump = COREDUMP_IDLE
        self.logger = logger
        self.websocket_client = websocket_client
        self.elf_files = elf_files[0]

    @property
    def in_progress(self) -> bool:
        return bool(self._coredump_buffer)

    def _process_coredump(self):  # type: () -> None
        if self._decode_coredumps != COREDUMP_DECODE_INFO:
            raise NotImplementedError('process_coredump: %s not implemented' % self._decode_coredumps)
        coredump_file = None
        # On Windows, the temporary file can't be read unless it is closed.
        # Set delete=False and delete the file manually later.
        with tempfile.NamedTemporaryFile(mode='wb', delete=False) as coredump_file:
            coredump_file.write(self._coredump_buffer)
            coredump_file.flush()

        if self.websocket_client:
            self.logger.output_enabled = True
            note_print('Communicating through WebSocket')
            self.websocket_client.send({'event': 'coredump',
                                        'file': coredump_file.name,
                                        'prog': self.elf_files})
            note_print('Waiting for debug finished event')
            self.websocket_client.wait([('event', 'debug_finished')])
            note_print('Communications through WebSocket is finished')
        else:
            try:
                import esp_coredump
                coredump = esp_coredump.CoreDump(core=coredump_file.name, core_format='b64', prog=self.elf_files)
                f = io.StringIO()
                with redirect_stdout(f):
                    coredump.info_corefile()
                output = f.getvalue()
                self.logger.output_enabled = True
                self.logger.print(output.encode('utf-8'))
                self.logger.output_enabled = False  # Will be re-enabled in check_coredump_trigger_after_print
            except ImportError as e:
                warning_print('Failed to parse core dump info: '
                              f'Module {e.name} is not installed\n\n')
                self._print_unprocessed_coredump()
            except (Exception, SystemExit) as e:
                error_print(f'Failed to parse core dump info: {e}\n\n')
                self._print_unprocessed_coredump()

        if coredump_file is not None:
            try:
                os.unlink(coredump_file.name)
            except OSError as e:
                warning_print(f'Couldn\'t remote temporary core dump file ({e})')

    def _print_unprocessed_coredump(self) -> None:
        """Print unprocessed core dump data if there was any issue during processing."""
        self.logger.output_enabled = True
        self.logger.print(COREDUMP_UART_START + b'\n')
        self.logger.print(self._coredump_buffer)
        # end line will be printed in handle_serial_input

    def _check_coredump_trigger_before_print(self, line):  # type: (bytes) -> None
        if self._decode_coredumps == COREDUMP_DECODE_DISABLE:
            return
        if COREDUMP_UART_PROMPT in line:
            note_print('Initiating core dump!')
            self.event_queue.put((TAG_KEY, '\n'))
            return
        if COREDUMP_UART_START in line:
            note_print('Core dump started (further output muted)')
            self._reading_coredump = COREDUMP_READING
            self._coredump_buffer = b''
            self.logger.output_enabled = False
            return
        if COREDUMP_UART_END in line:
            self._reading_coredump = COREDUMP_DONE
            note_print('Core dump finished!', prefix='\n')
            self._process_coredump()
            return
        if self._reading_coredump == COREDUMP_READING:
            kb = 1024
            buffer_len_kb = len(self._coredump_buffer) // kb
            self._coredump_buffer += line.replace(b'\r', b'') + b'\n'
            new_buffer_len_kb = len(self._coredump_buffer) // kb
            if new_buffer_len_kb > buffer_len_kb:
                note_print('Received %3d kB...' % new_buffer_len_kb, newline='\r')

    def _check_coredump_trigger_after_print(self):  # type: () -> None
        if self._decode_coredumps == COREDUMP_DECODE_DISABLE:
            return

        # Re-enable output after the last line of core dump has been consumed
        if not self.logger.output_enabled and self._reading_coredump == COREDUMP_DONE:
            self._reading_coredump = COREDUMP_IDLE
            self.logger.output_enabled = True
            self._coredump_buffer = b''

    @contextmanager
    def check(self, line):  # type: (bytes) -> Generator
        self._check_coredump_trigger_before_print(line)
        try:
            yield
        finally:
            self._check_coredump_trigger_after_print()
