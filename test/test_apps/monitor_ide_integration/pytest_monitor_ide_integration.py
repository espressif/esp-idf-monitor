# SPDX-FileCopyrightText: 2022-2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Unlicense OR CC0-1.0
import itertools
import json
import logging
import multiprocessing
import os
import re
from typing import List

import pexpect
import pytest
from pytest_embedded import Dut
from SimpleWebSocketServer import SimpleWebSocketServer, WebSocket


class IDEWSProtocol(WebSocket):

    def handleMessage(self) -> None:
        try:
            j = json.loads(self.data)
        except Exception as e:
            logging.info(f'Server ignores error: {e}')
            return
        event = j.get('event')
        if event and 'prog' in j and ((event == 'gdb_stub' and 'port' in j) or
                                      (event == 'coredump' and 'file' in j)):
            payload = {'event': 'debug_finished'}
            self.sendMessage(json.dumps(payload))
            logging.info(f'Server sent: {payload}')
        else:
            logging.info(f'Server received: {j}')

    def handleConnected(self) -> None:
        logging.info(f'{self.address} connected to server')

    def handleClose(self) -> None:
        logging.info(f'{self.address} closed the connection')


class WebSocketServer(object):
    HOST = '127.0.0.1'

    def run(self, port) -> None:
        # port=None -> pick random available port
        server = SimpleWebSocketServer(host=self.HOST, port=None, websocketclass=IDEWSProtocol)
        _, port.value = server.serversocket.getsockname()

        while not self.exit_event.is_set():
            server.serveonce()

        server.close()

    def __init__(self) -> None:
        # create a shared integer ('i') with a default value 0
        self.port = multiprocessing.Value('i', 0)
        self.exit_event = multiprocessing.Event()
        self.proc = multiprocessing.Process(target=self.run, args=(self.port,))
        self.proc.start()

    def teardown(self) -> None:
        self.exit_event.set()
        self.proc.join(10)
        if self.proc.is_alive():
            logging.info('Process cannot be joined')


@pytest.fixture(scope='module')
def webSocketServer():
    server = WebSocketServer()
    yield server
    server.teardown()


@pytest.mark.esp32
@pytest.mark.generic
@pytest.mark.parametrize('config', ['gdb_stub', 'coredump'], indirect=True)
def test_monitor_ide_integration(config: str, coverage_run: List[str], dut: Dut, webSocketServer: WebSocketServer) -> None:
    # The port needs to be closed because esp_idf_monitor will connect to it
    dut.serial.close()

    monitor_cmd = ' '.join(itertools.chain(
        coverage_run,
        ['-m', 'esp_idf_monitor', os.path.join(dut.app.binary_path, 'panic.elf'), '--port', str(dut.serial.port),
         '--ws', f'ws://{webSocketServer.HOST}:{webSocketServer.port.value}']
    ))
    monitor_log_path = os.path.join(dut.logdir, 'monitor.txt')

    with open(monitor_log_path, 'w') as log, pexpect.spawn(monitor_cmd,
                                                           logfile=log,
                                                           timeout=5,
                                                           encoding='utf-8',
                                                           codec_errors='ignore') as p:
        p.expect(re.compile(r'Guru Meditation Error'), timeout=10)
        p.expect_exact('--- Communicating through WebSocket')
        # The elements of dictionary can be printed in different order depending on the Python version.
        p.expect(re.compile(r"WebSocket sent: \{.*'event': '" + config + "'"))
        p.expect_exact('--- Waiting for debug finished event')
        p.expect(re.compile(r"WebSocket received: \{'event': 'debug_finished'\}"))
        p.expect_exact('--- Communications through WebSocket is finished')
        # end monitor and wait for proper termination to ensure complete coverage report
        p.sendcontrol(']')
        p.expect_exact(pexpect.EOF)


@pytest.mark.esp32
@pytest.mark.generic
@pytest.mark.parametrize('config', ['gdb_stub', 'coredump'], indirect=True)
def test_monitor_decode(config: str, coverage_run: List[str], dut: Dut) -> None:
    # The port needs to be closed because esp_idf_monitor will connect to it
    dut.serial.close()

    monitor_cmd = ' '.join(itertools.chain(
        coverage_run,
        ['-m', 'esp_idf_monitor', os.path.join(dut.app.binary_path, 'panic.elf'), '--port', str(dut.serial.port)]
    ))
    monitor_log_path = os.path.join(dut.logdir, 'monitor.txt')

    with open(monitor_log_path, 'w') as log, pexpect.spawn(monitor_cmd,
                                                           logfile=log,
                                                           timeout=5,
                                                           encoding='utf-8',
                                                           codec_errors='ignore') as p:
        p.expect(re.compile(r'Guru Meditation Error'), timeout=10)
        if config == 'gdb_stub':
            p.expect_exact('Entering gdb stub now.')
            p.expect_exact('$T0b#e6')
            p.expect_exact('(gdb)')
            p.sendline('quit')
            p.expect_exact('Quit anyway? (y or n)')
            p.sendline('y')
        else:
            p.expect_exact('--- Core dump started (further output muted)')
            p.expect_exact('--- Core dump finished!')
            p.expect_exact('==================== ESP32 CORE DUMP START ====================')
            p.expect_exact('===================== ESP32 CORE DUMP END =====================')
        # end monitor and wait for proper termination to ensure complete coverage report
        p.sendcontrol(']')
        p.expect_exact(pexpect.EOF)
