# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import collections
import math
import queue  # noqa: F401
import re
import sys
import threading
import time
from typing import Optional  # noqa: F401

from esp_pylib.logger import log
from rich.markup import escape

from .console_parser import ConsoleParser  # noqa: F401
from .constants import CMD_APP_FLASH
from .constants import CMD_ENTER_BOOT
from .constants import CMD_FLASH_ALL
from .constants import CMD_MAKE
from .constants import CMD_OUTPUT_TOGGLE
from .constants import CMD_RESET
from .constants import CMD_STOP
from .constants import CMD_TOGGLE_LOGGING
from .constants import CMD_TOGGLE_TIMESTAMPS
from .constants import EXIT_EXPECT_TIMEOUT
from .constants import EXIT_SCRIPT_ERROR
from .constants import TAG_CMD
from .constants import TAG_KEY
from .stoppable_thread import StoppableThread

# Interval for checking the alive flag while sleeping or parked
POLL_INTERVAL = 0.1

# Number of recently decoded serial lines kept so that 'expect' can match output
# that was already received when the command is reached (bounded to limit memory)
EXPECT_BUFFER_LINES = 1024


class CommandReader(StoppableThread):
    """Read line-based commands from a non-TTY stdin (pipe, file) and push the
    corresponding events to the event queue, until EOF or the 'exit' command.

    This is the Command-Stream Mode counterpart of ConsoleReader and allows
    the monitor to be driven by a script or another program::

        printf 'reset\\nsleep 10\\nexit\\n' | python -m esp_idf_monitor /dev/ttyUSB0

    EOF exits the monitor only if some input was read before it (the script
    has finished). When stdin is empty from the start (e.g. /dev/null), there
    is no script and the monitor keeps watching the serial output until it is
    stopped by a signal (Ctrl+C, SIGTERM).

    The 'expect' command blocks the script until a serial line matches the
    given regular expression, so 'expect PATTERN' as the last command exits
    the monitor when the pattern is seen (serial output is printed the whole
    time). An optional ``--timeout <seconds>`` flag bounds the wait: when the
    pattern does not appear in time, an error is reported and the script is
    aborted (the rest of it would run at a wrong moment), so the monitor stops
    instead of blocking forever::

        printf 'reset\\nexpect --timeout 10 ALL TESTS PASSED\\n' | python -m esp_idf_monitor /dev/ttyUSB0

    Note: 'expect' also matches output that was already received when the
    command is reached - a bounded look-back buffer of recent lines is scanned
    first, consuming up to the match - so a line printed just before the command
    is not missed.

    All events are pushed to the event queue (never to the command queue) so
    that commands are executed strictly in the order in which they were read.
    Empty lines and lines starting with '#' are ignored. Every processed
    command is echoed to stderr, so the progress of the script is visible
    even when stdout is redirected to a file.
    """

    COMMANDS = {
        'reset': CMD_RESET,  # reset the chip via RTS
        'flash': CMD_MAKE,  # run make/idf.py flash (fast reflash by default)
        'flash-all': CMD_FLASH_ALL,  # run make/idf.py flash -a (full flash)
        'app-flash': CMD_APP_FLASH,  # run make/idf.py app-flash
        'output': CMD_OUTPUT_TOGGLE,  # toggle output display
        'log': CMD_TOGGLE_LOGGING,  # toggle logging to file
        'timestamps': CMD_TOGGLE_TIMESTAMPS,  # toggle timestamps
        'bootloader': CMD_ENTER_BOOT,  # reset the chip into bootloader
    }

    def __init__(self, event_queue, parser):
        # type: (queue.Queue, ConsoleParser) -> None
        super().__init__()
        self.event_queue = event_queue
        self.parser = parser
        # Process exit code the monitor should end with. Written here before the
        # stop is queued and read by the main thread once the main loop is over,
        # so the queue orders the two and no extra synchronization is needed.
        self.exit_code = 0
        # State of a pending 'expect' command. Lines decoded from the serial port
        # are delivered to observe_line() by the main thread; recent lines are
        # kept in a bounded look-back buffer so that 'expect' can also match
        # output that was already received when the command is reached (otherwise
        # the pattern would have to be armed before the line is decoded, which is
        # racy). _expect_lock guards all of this shared state.
        self._expect_lock = threading.Lock()
        self._observed = collections.deque(maxlen=EXPECT_BUFFER_LINES)  # type: collections.deque
        self._expect_pattern = None  # type: Optional[re.Pattern]
        self._expect_matched = threading.Event()

    def start(self):
        # type: () -> None
        # Same as StoppableThread.start(), but the thread is created as a
        # daemon: a thread blocked in sys.stdin.readline() cannot be
        # interrupted, so it must not prevent the interpreter from exiting.
        if self._thread is None:
            self._thread = threading.Thread(target=self._run_outer, daemon=True)
            self._thread.start()

    def stop(self):
        # type: () -> None
        # Overridden without the join: the thread may be blocked in readline()
        # and as a daemon it exits on its own or together with the process.
        self._thread = None

    def run(self):
        # type: () -> None
        commands_read = False
        while self.alive:
            line = sys.stdin.readline()
            if not line:
                if not commands_read:
                    # Nothing was piped in (e.g. stdin is /dev/null in CI or
                    # Docker without -i): there is no script to follow, keep
                    # watching the serial output. The monitor is then stopped
                    # from the outside: Ctrl+C, SIGTERM (docker stop, CI job
                    # timeout) or e.g. the timeout(1) utility.
                    log.print('No commands on standard input, watching serial output only...', style='yellow')
                    break
                # EOF after a script: the script has finished, exit the monitor
                log.print('EOF received on standard input, exiting...', style='yellow')
                self._push_stop()
                break
            commands_read = True
            if not self._handle_line(line.rstrip('\r\n')):
                break
        # Park until this reader is stopped. In the watch-only case this keeps
        # the monitor running; after a script it lets the main loop process the
        # already queued commands and pending serial data before it stops this
        # reader (exiting the thread right away would mark the reader as dead
        # and terminate the main loop prematurely).
        while self.alive:
            time.sleep(POLL_INTERVAL)

    def _handle_line(self, line):
        # type: (str) -> bool
        """Process a single command line. Returns False to stop reading."""
        if not line or line.startswith('#'):
            return True
        # echo every command to stderr, so the progress of the script is
        # visible even when stdout is redirected to a file
        log.print(f'Command: {line!r}', style='yellow')
        command, _, argument = line.partition(' ')
        command = command.lower()
        if command == 'send':
            # send the argument followed by an end of line to the device
            self.event_queue.put((TAG_KEY, self.parser.translate_eol(argument + '\n')))
        elif command == 'sleep':
            try:
                duration = float(argument)
            except ValueError:
                log.err(f'Invalid sleep duration: {argument!r}')
                return True
            self._sleep(duration)
        elif command == 'expect':
            timeout = None  # type: Optional[float]
            regex_str = argument
            if argument.startswith('--timeout'):
                parts = argument.split(None, 2)  # ['--timeout', '<seconds>', '<regex...>']
                if len(parts) < 2:
                    log.err("'expect --timeout' requires a <seconds> value and a <regex>, exiting...")
                    self._push_stop(EXIT_SCRIPT_ERROR)
                    return False
                if len(parts) < 3 or not parts[2].strip():
                    log.err("'expect --timeout' requires a <regex> after the timeout value, exiting...")
                    self._push_stop(EXIT_SCRIPT_ERROR)
                    return False
                try:
                    timeout = float(parts[1])
                except ValueError:
                    log.err(f'Invalid expect timeout value: {parts[1]!r} (must be a positive number), exiting...')
                    self._push_stop(EXIT_SCRIPT_ERROR)
                    return False
                if not math.isfinite(timeout) or timeout <= 0:
                    log.err(f'Invalid expect timeout value: {parts[1]!r} (must be a finite number > 0), exiting...')
                    self._push_stop(EXIT_SCRIPT_ERROR)
                    return False
                regex_str = parts[2]
            try:
                pattern = re.compile(regex_str)
            except re.error as e:
                # proceeding without the wait could execute the rest of the
                # script at a wrong moment, better to fail fast
                log.err(f'Invalid expect pattern {escape(regex_str)!r}: {e}, exiting...')
                self._push_stop(EXIT_SCRIPT_ERROR)
                return False
            if not self._expect(pattern, timeout=timeout):
                self._push_stop(EXIT_EXPECT_TIMEOUT)
                return False
        elif command == 'exit':
            self._push_stop()
            return False
        elif command in self.COMMANDS:
            self.event_queue.put((TAG_CMD, self.COMMANDS[command]))
        else:
            log.err(f'Unknown command: {line!r}')
        return True

    def observe_line(self, line):
        # type: (str) -> None
        """Called from the main thread for every decoded serial line. Wakes up a
        pending 'expect' when the line matches its pattern, otherwise records the
        line in the look-back buffer for a later 'expect'."""
        # strip the line ending so that patterns anchored with $ match CRLF lines
        line = line.rstrip('\r\n')
        with self._expect_lock:
            if self._expect_pattern is not None:
                if self._expect_pattern.search(line):
                    self._expect_pattern = None
                    self._expect_matched.set()
            else:
                self._observed.append(line)

    def _expect(self, pattern, timeout=None):
        # type: (re.Pattern, Optional[float]) -> bool
        """Block until a serial line matching the pattern is observed, or until
        the optional timeout elapses, while staying responsive to stop(). Output
        that was already received before this command was reached is matched as
        well: the look-back buffer is scanned first (consuming up to and
        including the match), and only if the pattern is not there yet is it
        armed to wait for new lines. Serial output continues to be processed and
        printed by the main loop in the meantime.

        Returns False only when the timeout elapsed without a match, so that the
        caller can abort the script. Being stopped while waiting (Ctrl+C, SIGTERM)
        is not a timeout: the monitor is shutting down anyway, so True is
        returned and no error is reported."""
        with self._expect_lock:
            self._expect_matched.clear()
            buffered = list(self._observed)
            self._observed.clear()
            matched_idx = next((i for i, ln in enumerate(buffered) if pattern.search(ln)), None)
            if matched_idx is not None:
                # keep the lines after the match for a subsequent 'expect'
                self._observed.extend(buffered[matched_idx + 1 :])
                self._expect_matched.set()
            else:
                self._expect_pattern = pattern
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        try:
            while self.alive and not self._expect_matched.wait(POLL_INTERVAL):
                if deadline is not None and time.monotonic() >= deadline:
                    break
        finally:
            with self._expect_lock:
                self._expect_pattern = None
        if self._expect_matched.is_set():
            log.print(f'Expect pattern {pattern.pattern!r} matched, continuing...', style='yellow')
            return True
        if deadline is not None and time.monotonic() >= deadline:
            log.err(f'Expect pattern {pattern.pattern!r} timed out after {timeout}s, exiting...')
            return False
        return True  # stopped while waiting, not a timeout

    def _push_stop(self, exit_code=0):
        # type: (int) -> None
        if exit_code:
            self.exit_code = exit_code
        self.event_queue.put((TAG_CMD, CMD_STOP))

    def _sleep(self, duration):
        # type: (float) -> None
        """Sleep for the given duration (serial output continues to be
        processed by the main loop), while staying responsive to stop()."""
        deadline = time.monotonic() + duration
        while self.alive:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(POLL_INTERVAL, remaining))
