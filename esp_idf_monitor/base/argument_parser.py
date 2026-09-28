# SPDX-FileCopyrightText: 2015-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""rich-click CLI definition for ``idf-monitor``."""

from __future__ import annotations

import os
import sys
from typing import Any
from typing import Callable

import rich_click as click
from esp_pylib.cli_types import BaudRateType
from esp_pylib.cli_types import SerialPortType

from .constants import DEFAULT_PRINT_FILTER
from .constants import DEFAULT_TARGET_RESET
from .constants import DEFAULT_TOOLCHAIN_PREFIX
from .constants import PANIC_DECODE_BACKTRACE
from .constants import PANIC_DECODE_DISABLE
from .coredump import COREDUMP_DECODE_DISABLE
from .coredump import COREDUMP_DECODE_INFO


def _default_port_help() -> str:
    """Return the platform-dependent help text for ``--port``."""
    suffix = ' Defaults to `/dev/ttyUSB0` if connected.' if sys.platform == 'linux' else ''
    return 'Serial port device. If not set, a connected port will be used.' + suffix


@click.command(
    'idf-monitor',
    help='idf_monitor - a serial output monitor for esp-idf',
    context_settings={'help_option_names': ['-h', '--help']},
)
@click.option(
    '--port',
    '-p',
    type=SerialPortType(),
    envvar='ESPTOOL_PORT',
    help=_default_port_help(),
)
@click.option(
    '--pick',
    is_flag=True,
    envvar='ESP_IDF_MONITOR_PICK',
    default=False,
    help='If --port is not set, choose the port from a list of connected ports instead of using the '
    'first one found. Ignored when not running in a terminal.',
)
@click.option(
    '--no-reset',
    is_flag=True,
    envvar='ESP_IDF_MONITOR_NO_RESET',
    default=not DEFAULT_TARGET_RESET,
    help='Do not reset the chip on monitor startup',
)
@click.option(
    '--disable-address-decoding',
    '-d',
    is_flag=True,
    default=lambda: os.getenv('ESP_MONITOR_DECODE') == '0',
    help="Don't print lines about decoded addresses from the application ELF file",
)
@click.option(
    '--baud',
    '-b',
    type=BaudRateType(),
    envvar=['IDF_MONITOR_BAUD', 'MONITORBAUD'],
    default=115200,
    help='Serial port baud rate',
)
@click.option(
    '--make',
    '-m',
    type=str,
    default='make',
    help='Command to run make',
)
@click.option(
    '--encrypted',
    is_flag=True,
    default=False,
    help='Use encrypted targets while running make',
)
@click.option(
    '--toolchain-prefix',
    type=str,
    default=DEFAULT_TOOLCHAIN_PREFIX,
    help='Triplet prefix to add before cross-toolchain names',
)
@click.option(
    '--eol',
    type=click.Choice(['CR', 'LF', 'CRLF'], case_sensitive=False),
    default=None,
    help='End of line to use when sending to the serial port. Defaults to LF for Linux targets and CR otherwise.',
)
@click.option(
    '--rom-elf-file',
    type=str,
    default=None,
    help='ELF file of target ROM for address decoding. '
    'If not specified, autodetection is attempted based on the IDF_PATH and ESP_ROM_ELF_DIR env vars.',
)
@click.option(
    '--print_filter',
    type=str,
    envvar='ESP_IDF_MONITOR_PRINT_FILTER',
    default=DEFAULT_PRINT_FILTER,
    help='Filtering string',
)
@click.option(
    '--decode-coredumps',
    type=click.Choice([COREDUMP_DECODE_INFO, COREDUMP_DECODE_DISABLE]),
    default=COREDUMP_DECODE_INFO,
    help='Handling of core dumps found in serial output',
)
@click.option(
    '--decode-panic',
    type=click.Choice([PANIC_DECODE_BACKTRACE, PANIC_DECODE_DISABLE]),
    default=PANIC_DECODE_DISABLE,
    help='Handling of panic handler info found in serial output',
)
@click.option(
    '--target',
    type=str,
    envvar='IDF_TARGET',
    default='esp32',
    help='Target name (used when stack dump decoding is enabled)',
)
@click.option(
    '--revision',
    type=int,
    default=0,
    help='Revision of the target',
)
@click.option(
    '--ws',
    type=str,
    envvar='ESP_IDF_MONITOR_WS',
    default=None,
    help='WebSocket URL for communicating with IDE tools for debugging purposes',
)
@click.option(
    '--timestamps',
    is_flag=True,
    default=False,
    help='Add timestamp for each line',
)
@click.option(
    '--timestamp-format',
    type=str,
    envvar='ESP_IDF_MONITOR_TIMESTAMP_FORMAT',
    default='%Y-%m-%d %H:%M:%S',
    help='Set a strftime()-compatible timestamp format',
)
@click.option(
    '--force-color',
    is_flag=True,
    default=False,
    help='Always colored monitor output, even if output is redirected.',
)
@click.option(
    '--disable-auto-color',
    is_flag=True,
    default=False,
    help='Disable automatic color addition to monitor output based on the log level',
)
@click.option(
    '--open-port-attempts',
    type=int,
    default=1,
    help=(
        'Number of attempts to wait for the port to appear (useful if the device is not connected or in deep '
        'sleep). The delay between attempts can be defined by the `reconnect_delay` option in a configuration file '
        '(by default 0.5 sec). Use 0 for infinite attempts.'
    ),
)
@click.option(
    '--save-log',
    '-s',
    is_flag=True,
    default=False,
    help='Save log of monitor',
)
@click.argument('elf_files', nargs=-1, type=str)
@click.pass_context
def cli(ctx: click.Context, **kwargs: Any) -> None:
    """Click entry point — delegates to the implementation registered on the context.

    The runner is stashed on ``ctx.obj`` so the same ``cli`` definition
    can be used both from the production entry point (which sets
    ``ctx.obj`` to `esp_idf_monitor.idf_monitor._run_monitor`) and
    from tests that want to invoke the command without running the
    monitor.
    """
    runner = ctx.obj if callable(ctx.obj) else None
    if runner is None:
        # No runner installed: behave like ``-h`` — no behaviour change but
        # don't crash, e.g. when invoked via ``cli.main([..], standalone_mode=False)``
        # from a test that just wants the parsed arguments.
        ctx.invoked_subcommand = None
        ctx.parsed_kwargs = kwargs  # type: ignore[attr-defined]
        return
    runner(**kwargs)


def invoke(runner: Callable[..., Any], argv: tuple[str, ...] | None = None) -> None:
    """Invoke the click command with a runner callback.

    Production code calls this from ``idf_monitor.main``; tests can use
    the same path with a stub runner to inspect what kwargs the CLI
    produced for a given ``argv``.
    """
    cli.main(args=argv, obj=runner, standalone_mode=True)
