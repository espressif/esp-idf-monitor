# SPDX-FileCopyrightText: 2023-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
import re
import subprocess
from typing import List  # noqa: F401

from esp_pylib.logger import log


class GDBHelper:
    def __init__(self, toolchain_prefix, elf_files, port, baud_rate):
        # type: (str, List[str], int, int) -> None
        self._gdb_buffer = b''  # type: bytes
        self._gdb_exit = False  # type: bool
        self.toolchain_prefix = toolchain_prefix
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
        log.print('')  # newline
        try:
            cmd = [
                f'{self.toolchain_prefix}gdb',
                '-ex',
                f'set serial baud {self.baud_rate}',
                '-ex',
                f'target remote {self.port}',
                self.elf_files[0],
            ]
            for elf_file in self.elf_files[1:]:
                cmd.append('-ex')
                cmd.append(f'add-symbol-file {elf_file}')
            # Here we handling GDB as a process
            # Open GDB process
            try:
                process = subprocess.Popen(cmd, cwd='.')
            except KeyboardInterrupt:
                pass
            # We ignore Ctrl+C interrupt from external process and wait response util GDB will be finished.
            while True:
                try:
                    process.wait()
                    break
                except KeyboardInterrupt:
                    pass  # We ignore the Ctrl+C
            self.gdb_exit = True
        except OSError as e:
            log.err(f'{" ".join(cmd)}: {e}')
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
        """Return True when ``line`` contains a valid gdbstub stop packet.

        Detection only — IDE / CLI handoff is done by the monitor after it
        pauses the serial reader.
        """
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
                return True
            log.err(f'Malformed gdb message... calculated checksum {chsum:02x} received {calc_chsum:02x}')
        return False
