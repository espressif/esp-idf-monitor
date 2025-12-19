# SPDX-FileCopyrightText: 2025 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

"""
Secure command executor for monitor-triggered CLI commands.

This module provides a controlled interface for executing pre-approved commands
found in monitor log lines, while preventing arbitrary code execution.

Expected format in the log line:

    ... IDF_MONITOR_EXECUTE_<TYPE> <ARGS>

For example:

    I (311) example: IDF_MONITOR_EXECUTE_ESPEFUSE_SUMMARY EFSR:esp32:300:AAAA...
"""

import os
import shlex
import subprocess

from .output_helpers import note_print
from .output_helpers import warning_print

# Prefix used in log lines to indicate an executable monitor command
MONITOR_EXECUTE_PREFIX = 'IDF_MONITOR_EXECUTE_'


class SecureMonitorCommandExecutor:
    """
    Secure executor for commands embedded in monitor log lines.

    The executor:
      - parses log lines for MONITOR_EXECUTE_PREFIX,
      - extracts the command type and arguments that follow the marker,
      - maps the type to a fixed command template,
      - executes the command if it is allowed.

    This prevents arbitrary commands from being run based solely on device output.
    """

    def __init__(self, logger) -> None:
        """
        Initialize the command executor.

        Args:
            logger: Logger instance used to print command output.
        """
        self._logger = logger
        self._incomplete_line = ''
        self.enable = True
        # Allowed commands keyed by TYPE after IDF_MONITOR_EXECUTE_
        # The placeholder '{}' is filled with the argument string from the log line
        self._allowed_cmds = {
            'ESPEFUSE_SUMMARY': 'espefuse --token {} summary --active',
            'ESPEFUSE_DUMP': 'espefuse --token {} dump',
        }

    def execute_from_log_line(self, chunk: bytes) -> None:
        """
        Consume a chunk of bytes from the monitor and execute an embedded
        command if a *complete* line with MONITOR_EXECUTE_PREFIX is present.

        This function is robust to partial lines: it accumulates data until
        a newline is seen, then processes whole lines one by one.
        """
        if not self.enable:
            return

        # log output is ASCII-like; ignore any problematic bytes
        text = chunk.decode('ascii', errors='ignore')

        if '\n' not in text:
            # No complete line yet; accumulate
            self._incomplete_line += text
            return

        # Complete line(s) present
        if self._incomplete_line:
            text = self._incomplete_line + text
            self._incomplete_line = ''

        self._process_complete_line(text)

    def _process_complete_line(self, line_text: str) -> None:
        """
        Handle a single *complete* log line (without trailing newline).
        If it contains a valid IDF_MONITOR_EXECUTE_<TYPE> command, execute it.
        """

        if MONITOR_EXECUTE_PREFIX not in line_text:
            return

        # Extract everything after the first occurrence of "IDF_MONITOR_EXECUTE_"
        parts = line_text.split(MONITOR_EXECUTE_PREFIX, 1)
        if len(parts) < 2:
            return

        # Split into TYPE and the rest (arguments)
        try:
            cmd_type, cmd_args = parts[1].strip().split(maxsplit=1)
        except ValueError:
            cmd_type, cmd_args = parts[1].strip(), ''

        template = self._allowed_cmds.get(cmd_type)
        if template is None:  # Unknown cmd type
            warning_print(f'Ignoring unknown monitor command type: "IDF_MONITOR_EXECUTE_{cmd_type}"')
            return

        if '{}' in template:
            cmd_args = cmd_args.strip()
            if not cmd_args:
                warning_print(f'Ignoring monitor command: no arguments provided for command "{cmd_type}": "{template}"')
                return
            formatted = template.format(cmd_args)
        else:
            formatted = template

        cmd_argv = shlex.split(formatted)  # Convert to argv list

        note_print(f'Executing monitor command: {" ".join(cmd_argv)}')

        try:
            output = subprocess.check_output(cmd_argv, stderr=subprocess.STDOUT, env=os.environ, shell=False)
            self._logger.print(output)
        except subprocess.CalledProcessError as e:
            warning_print(f'Command failed: {e}\n{e.output.decode(errors="ignore")}')
        except OSError as e:
            warning_print(f'Failed to execute command: {e}')
