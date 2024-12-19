# SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Unlicense OR CC0-1.0
import itertools
import os
import re
from typing import List

import pexpect
import pytest
from pytest_embedded import Dut


@pytest.mark.generic
@pytest.mark.esp32
@pytest.mark.parametrize('config', [
    'addr_lookup_in_app',
    'addr_lookup_in_ROM',
], indirect=True)
def test_monitor_addr_lookup(config: str, coverage_run: List[str], dut: Dut) -> None:
    # The port needs to be closed because esp_idf_monitor will connect to it
    dut.serial.close()

    monitor_args = ['--port', str(dut.serial.port), os.path.join(dut.app.binary_path, 'monitor_addr_lookup.elf')]
    if config == 'addr_lookup_in_app':
        # add only for in-app address lookup to test both multi elf file and single elf file support
        monitor_args.append(os.path.join(dut.app.binary_path, 'bootloader', 'bootloader.elf'))
    monitor_cmd = ' '.join(itertools.chain(coverage_run, ['-m', 'esp_idf_monitor'], monitor_args))
    monitor_log_path = os.path.join(dut.logdir, 'monitor.txt')

    with open(monitor_log_path, 'w') as log, pexpect.spawn(monitor_cmd, logfile=log, timeout=5, encoding='utf-8', codec_errors='ignore') as p:
        p.expect_exact('main_task: Calling app_main()')

        ADDRESS = r'0x[a-f0-9]{8}'
        FUNC_NAME = r'[a-zA-Z_][\w]*'

        p.expect(re.compile(rf'app_main is running from ({ADDRESS})'))
        main_addr = p.match.group(1)
        p.expect_exact(f'{main_addr}: app_main at')

        if config == 'addr_lookup_in_app':
            # reset the app to make sure that the bootloader address is caught
            p.sendcontrol('T')
            p.sendcontrol('R')
            # check for multi elf file support, this address should not be found in the bootloader elf
            p.expect(re.compile(rf'entry ({ADDRESS})'))
            addr = p.match.group(1)
            p.expect_exact(f'{addr}: call_start_cpu0 at')

            p.expect(re.compile(rf'Initializer function at ({ADDRESS})'))
            addr = p.match.group(1)
            p.expect_exact(f'--- {addr}: initialize at')

            p.expect(re.compile(rf'Got \d+ stored at ({ADDRESS}) and ({ADDRESS}) from a function from ({ADDRESS})'))
            var1 = p.match.group(1)
            var2 = p.match.group(2)
            func = p.match.group(3)
            match_index = p.expect([str(var1), str(var2), pexpect.TIMEOUT])
            assert match_index == 2  # should be TIMEOUT because addr2line should not match addresses of variables
            p.expect_exact(f'--- {func}: get_random_number at')

            # no new line interleave test
            p.expect_exact(f'Function pointer at {func}; main at {main_addr}; no new line')  # new line should be inserted here and decode is on separate line
            # make sure that both decode lines contain the correct prefix
            p.expect_exact(f'--- {func}: get_random_number at')
            p.expect_exact(f'--- {main_addr}: app_main at')
            p.expect_exact('new line now')

            p.expect_exact('This is the end of the report')

        elif config == 'addr_lookup_in_ROM':
            p.expect_exact('Crashing now!')

            p.expect(re.compile(rf'abort\(\) was called at PC ({ADDRESS}) on core 0'))
            addr = p.match.group(1)
            p.expect_exact(f'abort() was called at PC {addr} on core 0')

            p.expect(re.compile(rf'--- ({ADDRESS}): ({FUNC_NAME}) in ROM'))
            addr, func = p.match.group(1), p.match.group(2)
            p.expect_exact(f'{addr}: {func} in ROM')

        # end monitor and wait for proper termination to ensure complete coverage report
        p.sendcontrol(']')
        p.expect_exact(pexpect.EOF)
