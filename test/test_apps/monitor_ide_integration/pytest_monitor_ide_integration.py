# SPDX-FileCopyrightText: 2022-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Unlicense OR CC0-1.0
import itertools
import json
import logging
import multiprocessing
import os
import re
import threading
import time
from typing import Dict
from typing import List
from typing import Optional

import pexpect
import pytest
from pytest_embedded import Dut
from websockets.sync.server import ServerConnection
from websockets.sync.server import serve

# websockets 13 renamed this class to Server. 11.x, the last release that
# installs on Python 3.7, still exports WebSocketServer.
try:
    from websockets.sync.server import Server
except ImportError:
    from websockets.sync.server import WebSocketServer as Server


def _reply_for(message: str) -> Optional[str]:
    """Return the IDE reply for one monitor event, or None to stay silent."""
    try:
        parsed = json.loads(message)
    except Exception as e:
        logging.info(f'Server ignores error: {e}')
        return None
    if not isinstance(parsed, dict):
        logging.info(f'Server received: {parsed}')
        return None

    event = parsed.get('event')
    payload: Dict[str, object]
    if (
        event
        and 'prog' in parsed
        and ((event == 'gdb_stub' and 'port' in parsed) or (event == 'coredump' and 'file' in parsed))
    ):
        payload = {'event': 'debug_finished'}
    elif event == 'flash' and 'port' in parsed and 'target' in parsed and 'full' in parsed:
        payload = {'event': 'flash_finished', 'success': True}
    else:
        logging.info(f'Server received: {parsed}')
        return None

    logging.info(f'Server sent: {payload}')
    return json.dumps(payload)


def _handle_connection(websocket: ServerConnection) -> None:
    # Snapshot the peer address. getpeername() fails after the socket closes.
    address = websocket.remote_address
    logging.info(f'{address} connected to server')
    try:
        for message in websocket:
            if not isinstance(message, str):
                continue
            reply = _reply_for(message)
            if reply is not None:
                websocket.send(reply)
    finally:
        logging.info(f'{address} closed the connection')


class WebSocketServer:
    HOST = '127.0.0.1'

    def run(self, port) -> None:
        # port 0 -> the OS picks an available port
        with serve(_handle_connection, self.HOST, 0, compression=None) as server:
            _, port.value = server.socket.getsockname()
            watcher = threading.Thread(target=self._shutdown_when_stopped, args=(server,), daemon=True)
            watcher.start()
            server.serve_forever()

    def _shutdown_when_stopped(self, server: Server) -> None:
        self.exit_event.wait()
        server.shutdown()

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
    deadline = time.monotonic() + 5
    while server.port.value == 0:
        if time.monotonic() > deadline:
            server.teardown()
            raise RuntimeError('WebSocket test server did not bind a port')
        time.sleep(0.05)
    yield server
    server.teardown()


@pytest.mark.esp32
@pytest.mark.generic
@pytest.mark.parametrize('config', ['gdb_stub', 'coredump'], indirect=True)
def test_monitor_ide_integration(
    config: str, coverage_run: List[str], dut: Dut, webSocketServer: WebSocketServer
) -> None:
    # The port needs to be closed because esp_idf_monitor will connect to it
    dut.serial.close()

    monitor_cmd = ' '.join(
        itertools.chain(
            coverage_run,
            [
                '-m',
                'esp_idf_monitor',
                os.path.join(dut.app.binary_path, 'panic.elf'),
                '--port',
                str(dut.serial.port),
                '--ws',
                f'ws://{webSocketServer.HOST}:{webSocketServer.port.value}',  # type: ignore
            ],
        )
    )
    monitor_log_path = os.path.join(dut.logdir, 'monitor.txt')

    with open(monitor_log_path, 'w') as log, pexpect.spawn(
        monitor_cmd, logfile=log, timeout=5, encoding='utf-8', codec_errors='ignore'
    ) as p:
        p.expect(re.compile(r'Guru Meditation Error'), timeout=10)
        p.expect_exact('Communicating through WebSocket')
        # The elements of dictionary can be printed in different order depending on the Python version.
        p.expect(re.compile(r"WebSocket sent: \{.*'event': '" + config + "'"))
        p.expect_exact('Waiting for debug finished event')
        p.expect(re.compile(r"WebSocket received: \{'event': 'debug_finished'\}"))
        p.expect_exact('Communications through WebSocket is finished')
        # end monitor and wait for proper termination to ensure complete coverage report
        p.sendcontrol(']')
        p.expect_exact(pexpect.EOF)


@pytest.mark.esp32
@pytest.mark.generic
@pytest.mark.parametrize('config', ['gdb_stub'], indirect=True)
def test_monitor_ide_flash(coverage_run: List[str], dut: Dut, webSocketServer: WebSocketServer) -> None:
    # The port needs to be closed because esp_idf_monitor will connect to it
    dut.serial.close()

    monitor_cmd = ' '.join(
        itertools.chain(
            coverage_run,
            [
                '-m',
                'esp_idf_monitor',
                os.path.join(dut.app.binary_path, 'panic.elf'),
                '--port',
                str(dut.serial.port),
                '--ws',
                f'ws://{webSocketServer.HOST}:{webSocketServer.port.value}',  # type: ignore
            ],
        )
    )
    monitor_log_path = os.path.join(dut.logdir, 'monitor.txt')
    env = os.environ.copy()
    env['ESP_IDF_MONITOR_WS_FEATURES'] = 'flash'

    with open(monitor_log_path, 'w') as log, pexpect.spawn(
        monitor_cmd, logfile=log, timeout=5, encoding='utf-8', codec_errors='ignore', env=env
    ) as p:
        p.expect(re.compile(r'esp-idf-monitor'), timeout=10)
        # Menu flash is Ctrl+T then Ctrl+F. The test app panics after two
        # seconds, which stops the console reader, so send the shortcut now.
        p.sendcontrol('t')
        p.sendcontrol('f')
        p.expect_exact('Communicating through WebSocket')
        p.expect(re.compile(r"WebSocket sent: \{.*'event': 'flash'"))
        p.expect_exact('Waiting for flash finished event')
        p.expect(re.compile(r"WebSocket received: \{.*'event': 'flash_finished'"))
        p.expect_exact('Communications through WebSocket is finished')
        p.sendcontrol(']')
        p.expect_exact(pexpect.EOF)


@pytest.mark.esp32
@pytest.mark.generic
@pytest.mark.parametrize('config', ['gdb_stub', 'coredump'], indirect=True)
def test_monitor_decode(config: str, coverage_run: List[str], dut: Dut) -> None:
    # The port needs to be closed because esp_idf_monitor will connect to it
    dut.serial.close()

    monitor_cmd = ' '.join(
        itertools.chain(
            coverage_run,
            ['-m', 'esp_idf_monitor', os.path.join(dut.app.binary_path, 'panic.elf'), '--port', str(dut.serial.port)],
        )
    )
    monitor_log_path = os.path.join(dut.logdir, 'monitor.txt')

    with open(monitor_log_path, 'w') as log, pexpect.spawn(
        monitor_cmd, logfile=log, timeout=5, encoding='utf-8', codec_errors='ignore'
    ) as p:
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
