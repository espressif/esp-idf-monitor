# SPDX-FileCopyrightText: 2015-2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0


import os
import queue  # noqa: F401
import sys
import time

from serial.tools.miniterm import Console  # noqa: F401

from .console_parser import ConsoleParser  # noqa: F401
from .constants import CMD_STOP, TAG_CMD
from .stoppable_thread import StoppableThread


class ConsoleReader(StoppableThread):
    """ Read input keys from the console and push them to the queue,
    until stopped.
    """

    def __init__(self, console, event_queue, cmd_queue, parser, test_mode):
        # type: (Console, queue.Queue, queue.Queue, ConsoleParser, bool) -> None
        super(ConsoleReader, self).__init__()
        self.console = console
        self.event_queue = event_queue
        self.cmd_queue = cmd_queue
        self.parser = parser
        self.test_mode = test_mode
        if sys.platform == 'win32':
            # This is a workaround for multi-byte characters causing the console to be killed by OS.
            # Miniterm is setting the code page to UTF-8 which in combination with multibyte Unicode characters
            # results in Critical Error in Windows: https://github.com/espressif/esp-idf/issues/12162
            # Note: UTF-8 characters seem to work even without this setting
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(console._saved_ocp)
            ctypes.windll.kernel32.SetConsoleCP(console._saved_icp)

    def run(self):
        # type: () -> None
        self.console.setup()
        if sys.platform != 'win32':
            # Use non-blocking busy read to avoid using insecure TIOCSTI from console.cancel().
            # TIOCSTI is not supported on kernels newer than 6.2.
            import termios
            new = termios.tcgetattr(self.console.fd)
            # new[6] - 'cc': a list of the tty special characters
            new[6][termios.VMIN] = 0  # minimum bytes to read
            new[6][termios.VTIME] = 2  # timer of 0.1 second granularity
            termios.tcsetattr(self.console.fd, termios.TCSANOW, new)
        try:
            while self.alive:
                try:
                    if os.name == 'nt' and not self.test_mode:
                        # Windows kludge: because the console.cancel() method doesn't
                        # seem to work to unblock getkey() on the Windows implementation.
                        #
                        # So we only call getkey() if we know there's a key waiting for us.
                        import msvcrt
                        while not msvcrt.kbhit() and self.alive:  # type: ignore
                            time.sleep(0.1)
                        if not self.alive:
                            break
                    elif self.test_mode:
                        # In testing mode the stdin is connected to PTY but is not used for input anything. For PTY
                        # the canceling by fcntl.ioctl isn't working and would hang in self.console.getkey().
                        # Therefore, we avoid calling it.
                        while self.alive:
                            time.sleep(0.1)
                        break
                    c = self.console.getkey()
                except KeyboardInterrupt:
                    c = '\x03'
                if c:
                    ret = self.parser.parse(c)
                    if ret is not None:
                        (tag, cmd) = ret
                        # stop command should be executed last
                        if tag == TAG_CMD and cmd != CMD_STOP:
                            self.cmd_queue.put(ret)
                        else:
                            self.event_queue.put(ret)

        finally:
            self.console.cleanup()
