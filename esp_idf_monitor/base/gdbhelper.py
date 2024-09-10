# SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
import re
import subprocess
from typing import List, Optional  # noqa: F401

from .logger import Logger  # noqa: F401
from .output_helpers import error_print, normal_print, note_print
from .web_socket_client import WebSocketClient  # noqa: F401


class GDBHelper:
    def __init__(self, toolchain_prefix, websocket_client, elf_files, port, baud_rate):
        # type: (str, Optional[WebSocketClient], List[str], int, int) -> None
        self._gdb_buffer = b''  # type: bytes
        self._gdb_exit = False  # type: bool
        self.toolchain_prefix = toolchain_prefix
        self.websocket_client = websocket_client
        self.elf_files = elf_files
        self.port = port
        self.baud_rate = baud_rate

    @property
    def gdb_buffer(self):  # type: () -> bytes
        return self._gdb_buffer

    @gdb_buffer.setter
    def gdb_buffer(self, value):  # type: (bytes) -> None
        self._gdb_buffer = value

    @property
    def gdb_exit(self):  # type: () -> bool
        return self._gdb_exit

    @gdb_exit.setter
    def gdb_exit(self, value):  # type: (bool) -> None
        self._gdb_exit = value

    def run_gdb(self):
        # type: () -> None
        normal_print('')
        try:
            cmd = ['%sgdb' % self.toolchain_prefix,
                   '-ex', 'set serial baud %d' % self.baud_rate,
                   '-ex', 'target remote %s' % self.port,
                   self.elf_files[0]]
            for elf_file in self.elf_files[1:]:
                cmd.append('-ex')
                cmd.append(f'add-symbol-file {elf_file}')
            # Here we handling GDB as a process
            # Open GDB process
            try:
                process = subprocess.Popen(cmd, cwd='.')
            except KeyboardInterrupt:
                pass
            # We ignore Ctrl+C interrupt form external process abd wait response util GDB will be finished.
            while True:
                try:
                    process.wait()
                    break
                except KeyboardInterrupt:
                    pass  # We ignore the Ctrl+C
            self.gdb_exit = True
        except OSError as e:
            error_print(f"{' '.join(cmd)}: {e}")
        except KeyboardInterrupt:
            pass  # happens on Windows, maybe other OSes
        finally:
            try:
                # on Linux, maybe other OSes, gdb sometimes seems to be alive even after wait() returns...
                process.terminate()
            except Exception:  # noqa
                pass
            try:
                # also on Linux, maybe other OSes, gdb sometimes exits uncleanly and breaks the tty mode
                subprocess.call(['stty', 'sane'])
            except Exception:  # noqa
                pass  # don't care if there's no stty, we tried...

    def check_gdb_stub_trigger(self, line):
        # type: (bytes) -> bool
        line = self.gdb_buffer + line
        self.gdb_buffer = b''
        m = re.search(b'\\$(T..)#(..)', line)  # look for a gdb "reason" for a break
        if m is not None:
            try:
                chsum = sum(ord(bytes([p])) for p in m.group(1)) & 0xFF
                calc_chsum = int(m.group(2), 16)
            except ValueError:  # payload wasn't valid hex digits
                return False
            if chsum == calc_chsum:
                if self.websocket_client:
                    note_print('Communicating through WebSocket')
                    self.websocket_client.send({'event': 'gdb_stub',
                                                'port': self.port,
                                                'prog': self.elf_files[0]})
                    note_print('Waiting for debug finished event')
                    self.websocket_client.wait([('event', 'debug_finished')])
                    note_print('Communications through WebSocket is finished')
                else:
                    return True
            else:
                error_print('Malformed gdb message... calculated checksum %02x received %02x' % (chsum, calc_chsum))
        return False
