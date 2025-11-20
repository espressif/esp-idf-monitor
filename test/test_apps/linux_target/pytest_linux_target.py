# SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Unlicense OR CC0-1.0
import itertools
import os
from typing import List

import pexpect
import pytest
from pytest_embedded import Dut

from esp_idf_monitor import __version__


@pytest.mark.generic
@pytest.mark.linux
def test_linux_target(coverage_run: List[str], dut: Dut) -> None:
    monitor_cmd = ' '.join(
        itertools.chain(coverage_run, ['-m', 'esp_idf_monitor', dut.serial.app.elf_file, '--target', 'linux'])
    )
    monitor_log_path = os.path.join(dut.logdir, 'monitor.txt')

    with open(monitor_log_path, 'w') as log, pexpect.spawn(
        monitor_cmd, logfile=log, timeout=5, encoding='utf-8', codec_errors='ignore'
    ) as p:
        p.expect_exact(f'--- esp-idf-monitor {__version__} on linux')
        p.expect_exact('Hello world!')
        # try some unsupported command for linux e.g. resetting the target
        p.sendcontrol('T')
        p.sendcontrol('R')
        p.expect_exact('--- Warning: Linux target does not support this command')
        # end monitor
        p.sendcontrol(']')
        # read the rest of the input
        lines = p.readlines()
        assert 'Exception' not in ''.join(lines)
