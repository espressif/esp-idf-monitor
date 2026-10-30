# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Host tests for IDE WebSocket handoff.

Covers:
* gdbstub detection vs IDE/CLI handoff (serial paused before notify)
* coredump WebSocket wait without pausing the serial reader
* flash delegated to the IDE only when ``ESP_IDF_MONITOR_WS_FEATURES`` opts in
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import call
from unittest.mock import patch

import pytest
import serial

import esp_idf_monitor.base.web_socket_client as wsc
from esp_idf_monitor.base.coredump import COREDUMP_DECODE_INFO
from esp_idf_monitor.base.coredump import COREDUMP_UART_END
from esp_idf_monitor.base.coredump import COREDUMP_UART_START
from esp_idf_monitor.base.coredump import CoreDump
from esp_idf_monitor.base.gdbhelper import GDBHelper
from esp_idf_monitor.base.logger import Logger
from esp_idf_monitor.base.serial_reader import SerialReader
from esp_idf_monitor.base.web_socket_client import WebSocketWaitInterrupted
from esp_idf_monitor.idf_monitor import SerialMonitor
from esp_idf_monitor.idf_monitor import _MonitorAbort
from esp_idf_monitor.idf_monitor import _parse_ws_features

# Valid gdbstub stop reason: payload ``T0b``, checksum 0xe6
GDB_STUB_PACKET = b'$T0b#e6'


def _elf(tmp_path) -> str:
    path = tmp_path / 'app.elf'
    path.write_bytes(b'\x7fELF')
    return str(path)


def _serial_mock() -> MagicMock:
    serial_instance = MagicMock()
    serial_instance.port = '/dev/ttyUSB0'
    serial_instance.baudrate = 115200
    serial_instance.is_open = True
    return serial_instance


def _make_monitor(
    tmp_path,
    websocket_client: Any | None = None,
    command_stream_mode: bool = True,
    ide_flash: bool = False,
) -> SerialMonitor:
    """Build a SerialMonitor with mocked serial reader / console."""
    with patch('esp_idf_monitor.idf_monitor.SerialReader') as reader_cls, patch(
        'esp_idf_monitor.idf_monitor.ConsoleReader'
    ), patch('esp_idf_monitor.idf_monitor.CommandReader'), patch('esp_idf_monitor.idf_monitor.miniterm.Console'), patch(
        'esp_idf_monitor.idf_monitor.miniterm.ConsoleBase'
    ):
        reader = MagicMock()
        reader_cls.return_value = reader
        monitor = SerialMonitor(
            _serial_mock(),
            [_elf(tmp_path)],
            '',
            websocket_client=websocket_client,
            command_stream_mode=command_stream_mode,
            enable_address_decoding=False,
            ide_flash=ide_flash,
        )
    # Ensure stop/start are trackable MagicMocks (SerialReader is mocked).
    monitor.serial_reader = reader
    return monitor


@pytest.fixture(params=['pylib', 'legacy'])
def recv_abort_client(request, monkeypatch):
    """Yield a real `WebSocketClient` and the list of connections it opened.

    The first connection raises `_MonitorAbort` from ``recv()``, which is
    where the SIGTERM handler raises while the monitor waits for the IDE.
    Later connections reply with ``debug_finished``, so a backend that retries
    the abort as a receive error returns instead of hanging the test.
    """
    connections: list[MagicMock] = []

    def connect(_url):
        conn = MagicMock()
        if connections:
            conn.recv.return_value = json.dumps({'event': 'debug_finished'})
        else:
            conn.recv.side_effect = _MonitorAbort
        connections.append(conn)
        return conn

    if request.param == 'legacy':
        monkeypatch.setattr(wsc, '_pylib_available', lambda: False)
        monkeypatch.setattr(wsc, 'websocket', MagicMock(create_connection=connect))
        yield wsc.WebSocketClient('ws://ide.invalid'), connections
        return

    if sys.version_info < (3, 8):
        pytest.skip('pylib backend requires Python >= 3.8')
    monkeypatch.setitem(sys.modules, 'websockets.sync.client', MagicMock(connect=connect))
    try:
        yield wsc.WebSocketClient('ws://ide.invalid'), connections
    finally:
        wsc._pylib_ws.set_ws_url(None)


class TestGdbHelperDetect:
    def test_valid_packet_returns_true(self):
        helper = GDBHelper('xtensa-esp32-elf-', ['prog.elf'], '/dev/ttyUSB0', 115200)
        assert helper.check_gdb_stub_trigger(GDB_STUB_PACKET) is True

    def test_invalid_checksum_returns_false(self):
        helper = GDBHelper('xtensa-esp32-elf-', ['prog.elf'], '/dev/ttyUSB0', 115200)
        assert helper.check_gdb_stub_trigger(b'$T0b#00') is False


class TestGdbStubIdeHandoff:
    def test_ws_pauses_serial_before_notify(self, tmp_path):
        ws = MagicMock()
        order: list[str] = []

        monitor = _make_monitor(tmp_path, websocket_client=ws)
        monitor.serial_reader.stop.side_effect = lambda: order.append('stop')
        monitor.serial_reader.start.side_effect = lambda: order.append('start')
        ws.roundtrip.side_effect = lambda *a, **k: order.append('roundtrip')

        with patch.object(monitor.gdb_helper, 'run_gdb') as run_gdb:
            monitor.check_gdb_stub_and_run(GDB_STUB_PACKET)

        assert order == ['stop', 'roundtrip', 'start']
        ws.roundtrip.assert_called_once_with(
            {'event': 'gdb_stub', 'port': '/dev/ttyUSB0', 'prog': _elf(tmp_path)},
            'debug_finished',
        )
        run_gdb.assert_not_called()

    def test_ctrl_c_keeps_port_released_until_debug_finished(self, tmp_path):
        ws = MagicMock()
        order: list[str] = []
        monitor = _make_monitor(tmp_path, websocket_client=ws)
        monitor.serial_reader.stop.side_effect = lambda: order.append('stop')
        monitor.serial_reader.start.side_effect = lambda: order.append('start')

        def roundtrip(*_args):
            order.append('roundtrip')
            raise WebSocketWaitInterrupted

        def wait(expect):
            order.append('wait')
            assert expect == [('event', 'debug_finished')]
            assert 'start' not in order
            return {'event': 'debug_finished'}

        ws.roundtrip.side_effect = roundtrip
        ws.wait.side_effect = wait

        monitor.check_gdb_stub_and_run(GDB_STUB_PACKET)

        assert order == ['stop', 'roundtrip', 'wait', 'start']

    def test_ctrl_c_before_send_reclaims_port(self, tmp_path):
        ws = MagicMock()
        monitor = _make_monitor(tmp_path, websocket_client=ws)
        ws.roundtrip.side_effect = KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            monitor.check_gdb_stub_and_run(GDB_STUB_PACKET)

        ws.wait.assert_not_called()
        monitor.serial_reader.start.assert_called_once()

    def test_second_ctrl_c_quits_without_reopening_port(self, tmp_path):
        ws = MagicMock()
        order: list[str] = []
        monitor = _make_monitor(tmp_path, websocket_client=ws)
        monitor.serial_reader.stop.side_effect = lambda: order.append('stop')
        monitor.serial_reader.start.side_effect = lambda: order.append('start')
        ws.roundtrip.side_effect = WebSocketWaitInterrupted

        def wait(_expect):
            order.append('wait')
            raise KeyboardInterrupt

        ws.wait.side_effect = wait

        monitor.console_reader.alive = True
        monitor.serial_reader.alive = True
        with patch.object(monitor, '_pre_start'), patch.object(
            monitor, '_main_loop', side_effect=lambda: monitor.check_gdb_stub_and_run(GDB_STUB_PACKET)
        ):
            monitor.main_loop()

        monitor.serial_reader.start.assert_not_called()
        assert order == ['stop', 'wait', 'stop']

    def test_sigterm_during_handoff_quits_without_reopening_port(self, tmp_path):
        """Command-Stream Mode maps SIGTERM to `_MonitorAbort` so one stop
        during handoff exits without reclaiming a port the IDE may hold."""
        ws = MagicMock()
        order: list[str] = []
        monitor = _make_monitor(tmp_path, websocket_client=ws, command_stream_mode=True)
        monitor.serial_reader.stop.side_effect = lambda: order.append('stop')
        monitor.serial_reader.start.side_effect = lambda: order.append('start')
        ws.roundtrip.side_effect = _MonitorAbort

        monitor.console_reader.alive = True
        monitor.serial_reader.alive = True
        with patch.object(monitor, '_pre_start'), patch.object(
            monitor, '_main_loop', side_effect=lambda: monitor.check_gdb_stub_and_run(GDB_STUB_PACKET)
        ), patch('esp_idf_monitor.idf_monitor.signal.signal') as signal_mock:
            monitor.main_loop()

        handler = signal_mock.call_args[0][1]
        with pytest.raises(_MonitorAbort):
            handler(15, None)
        monitor.serial_reader.start.assert_not_called()
        assert order == ['stop', 'stop']

    def test_sigterm_in_transport_recv_quits_without_reconnecting(self, tmp_path, recv_abort_client):
        """Both transports reconnect on ``except Exception``. The abort must pass
        through them instead of being retried as a receive error."""
        client, connections = recv_abort_client
        monitor = _make_monitor(tmp_path, websocket_client=client, command_stream_mode=True)

        with pytest.raises(_MonitorAbort):
            monitor.check_gdb_stub_and_run(GDB_STUB_PACKET)

        monitor.serial_reader.start.assert_not_called()
        assert len(connections) == 1

    def test_without_ws_runs_cli_gdb_when_interactive(self, tmp_path):
        monitor = _make_monitor(tmp_path, websocket_client=None, command_stream_mode=False)
        with patch.object(monitor.gdb_helper, 'run_gdb') as run_gdb:
            monitor.check_gdb_stub_and_run(GDB_STUB_PACKET)
        run_gdb.assert_called_once()

    def test_without_ws_warns_in_command_stream_mode(self, tmp_path):
        monitor = _make_monitor(tmp_path, websocket_client=None, command_stream_mode=True)
        with patch.object(monitor.gdb_helper, 'run_gdb') as run_gdb:
            monitor.check_gdb_stub_and_run(GDB_STUB_PACKET)
        run_gdb.assert_not_called()
        assert monitor._gdb_stub_warned is True


class TestCoredumpIdeHandoff:
    def test_ws_notifies_without_pausing_serial(self, tmp_path):
        ws = MagicMock()
        logger = MagicMock(spec=Logger)
        logger.output_enabled = True
        coredump = CoreDump(
            COREDUMP_DECODE_INFO,
            MagicMock(),
            logger,
            ws,
            [_elf(tmp_path)],
        )

        # Drive the end-of-coredump path used in production.
        with coredump.check(COREDUMP_UART_START):
            pass
        coredump._coredump_buffer = b'coredump-data'
        with coredump.check(COREDUMP_UART_END):
            pass

        ws.roundtrip.assert_called_once()
        payload = ws.roundtrip.call_args[0][0]
        assert payload['event'] == 'coredump'
        assert payload['prog'] == _elf(tmp_path)
        assert 'file' in payload
        assert ws.roundtrip.call_args[0][1] == 'debug_finished'


class TestKeyboardInterruptStillWritesCtrlC:
    """Ctrl+C handling is unchanged: still writes \\x03 to the chip."""

    def test_keyboard_interrupt_writes_ctrl_c_even_with_ws(self, tmp_path):
        ws = MagicMock()
        monitor = _make_monitor(tmp_path, websocket_client=ws, command_stream_mode=False)
        monitor.console_reader = MagicMock()
        monitor.console_reader.alive = True
        monitor.serial_reader.alive = True

        # Force one KeyboardInterrupt from _main_loop, then stop the loop.
        calls = {'n': 0}

        def fake_main_loop():
            calls['n'] += 1
            if calls['n'] == 1:
                raise KeyboardInterrupt
            monitor.console_reader.alive = False

        written: list[bytes] = []
        monitor.serial_write = lambda data: written.append(data)  # type: ignore[method-assign]
        with patch.object(monitor, '_main_loop', side_effect=fake_main_loop):
            with patch.object(monitor, '_pre_start'):
                monitor.main_loop()

        assert written and written[0] == b'\x03'
        ws.roundtrip.assert_not_called()


class TestParseWsFeatures:
    def test_unset_is_empty(self, monkeypatch):
        monkeypatch.delenv('ESP_IDF_MONITOR_WS_FEATURES', raising=False)
        assert _parse_ws_features() == set()

    def test_mixed_case_and_spaces(self):
        assert _parse_ws_features(' Flash , OTHER ') == {'flash', 'other'}

    def test_unknown_features_kept(self):
        assert _parse_ws_features('flash,future_thing') == {'flash', 'future_thing'}

    def test_empty_tokens_ignored(self):
        assert _parse_ws_features(',,flash,,') == {'flash'}


class TestFlashIdeEvents:
    def test_ws_without_opt_in_uses_subprocess(self, tmp_path):
        ws = MagicMock()
        monitor = _make_monitor(tmp_path, websocket_client=ws, ide_flash=False)
        with patch('esp_idf_monitor.idf_monitor.run_make') as local_make:
            monitor.run_make('flash')
        local_make.assert_called_once()
        ws.roundtrip.assert_not_called()

    def test_run_make_delegates_to_websocket(self, tmp_path):
        ws = MagicMock()
        ws.roundtrip.return_value = {'event': 'flash_finished', 'success': True}
        monitor = _make_monitor(tmp_path, websocket_client=ws, ide_flash=True)

        with patch('esp_idf_monitor.idf_monitor.run_make') as local_make:
            monitor.run_make('flash')

        local_make.assert_not_called()
        ws.roundtrip.assert_called_once_with(
            {'event': 'flash', 'port': '/dev/ttyUSB0', 'target': 'flash', 'full': False},
            'flash_finished',
        )

    def test_run_make_full_flash_sets_full_flag(self, tmp_path):
        ws = MagicMock()
        ws.roundtrip.return_value = {'event': 'flash_finished'}
        monitor = _make_monitor(tmp_path, websocket_client=ws, ide_flash=True)

        monitor.run_make('flash', env_extra={'IDF_FLASH_FULL': '1'})

        ws.roundtrip.assert_called_once_with(
            {'event': 'flash', 'port': '/dev/ttyUSB0', 'target': 'flash', 'full': True},
            'flash_finished',
        )

    def test_run_make_full_flag_ignores_zero(self, tmp_path):
        ws = MagicMock()
        ws.roundtrip.return_value = {'event': 'flash_finished'}
        monitor = _make_monitor(tmp_path, websocket_client=ws, ide_flash=True)

        monitor.run_make('flash', env_extra={'IDF_FLASH_FULL': '0'})

        ws.roundtrip.assert_called_once_with(
            {'event': 'flash', 'port': '/dev/ttyUSB0', 'target': 'flash', 'full': False},
            'flash_finished',
        )

    @pytest.mark.parametrize(
        'target',
        ['flash', 'app-flash', 'encrypted-flash', 'encrypted-app-flash'],
    )
    def test_run_make_flash_targets(self, tmp_path, target):
        ws = MagicMock()
        ws.roundtrip.return_value = {'event': 'flash_finished'}
        monitor = _make_monitor(tmp_path, websocket_client=ws, ide_flash=True)

        monitor.run_make(target)

        assert ws.roundtrip.call_args == call(
            {'event': 'flash', 'port': '/dev/ttyUSB0', 'target': target, 'full': False},
            'flash_finished',
        )

    def test_run_make_failure_logs_in_command_stream_mode(self, tmp_path):
        ws = MagicMock()
        ws.roundtrip.return_value = {'event': 'flash_finished', 'success': False}
        monitor = _make_monitor(tmp_path, websocket_client=ws, ide_flash=True, command_stream_mode=True)

        with patch('esp_idf_monitor.idf_monitor.log.err') as log_err:
            monitor.run_make('flash')

        log_err.assert_called_once_with('Flashing via IDE failed')

    def test_run_make_failure_prompts_in_interactive(self, tmp_path):
        ws = MagicMock()
        ws.roundtrip.return_value = {'event': 'flash_finished', 'success': False}
        monitor = _make_monitor(tmp_path, websocket_client=ws, ide_flash=True, command_stream_mode=False)
        order: list[str] = []
        monitor.serial_reader.stop.side_effect = lambda: order.append('serial-stop')
        monitor.serial_reader.start.side_effect = lambda: order.append('serial-start')
        monitor.console_reader.stop.side_effect = lambda: order.append('console-stop')
        monitor.console_reader.start.side_effect = lambda: order.append('console-start')

        def on_prompt(*_args, **_kwargs):
            order.append('prompt')

        with patch('esp_idf_monitor.idf_monitor.prompt_next_action', side_effect=on_prompt) as prompt:
            monitor.run_make('flash')

        prompt.assert_called_once()
        assert prompt.call_args[0][0] == 'Flashing via IDE failed'
        # The console reader must stay stopped while the prompt reads a key.
        assert order == ['serial-stop', 'console-stop', 'prompt', 'console-start', 'serial-start']

    @pytest.mark.parametrize('command_stream_mode', [False, True])
    def test_run_make_ctrl_c_keeps_waiting_for_flash_finished(self, tmp_path, command_stream_mode):
        ws = MagicMock()
        order: list[str] = []
        monitor = _make_monitor(
            tmp_path,
            websocket_client=ws,
            ide_flash=True,
            command_stream_mode=command_stream_mode,
        )
        monitor.serial_reader.stop.side_effect = lambda: order.append('stop')
        monitor.serial_reader.start.side_effect = lambda: order.append('start')

        def roundtrip(payload, _wait_event):
            order.append(payload['event'])
            raise WebSocketWaitInterrupted

        def wait(expect):
            order.append('wait')
            assert expect == [('event', 'flash_finished')]
            assert 'start' not in order
            return {'event': 'flash_finished', 'success': True}

        ws.roundtrip.side_effect = roundtrip
        ws.wait.side_effect = wait

        with patch('esp_idf_monitor.idf_monitor.prompt_next_action') as prompt:
            monitor.run_make('flash')

        prompt.assert_not_called()
        ws.roundtrip.assert_called_once()
        assert order == ['stop', 'flash', 'wait', 'start']

    def test_run_make_second_ctrl_c_quits_without_reopening_port(self, tmp_path):
        ws = MagicMock()
        order: list[str] = []
        monitor = _make_monitor(tmp_path, websocket_client=ws, ide_flash=True, command_stream_mode=True)
        monitor.serial_reader.stop.side_effect = lambda: order.append('stop')
        monitor.serial_reader.start.side_effect = lambda: order.append('start')
        ws.roundtrip.side_effect = WebSocketWaitInterrupted

        def wait(_expect):
            order.append('wait')
            raise KeyboardInterrupt

        ws.wait.side_effect = wait

        monitor.console_reader.alive = True
        monitor.serial_reader.alive = True
        with patch.object(monitor, '_pre_start'), patch.object(
            monitor, '_main_loop', side_effect=lambda: monitor.run_make('flash')
        ):
            monitor.main_loop()

        monitor.serial_reader.start.assert_not_called()
        assert order == ['stop', 'wait', 'stop']

    def test_run_make_without_ws_uses_subprocess(self, tmp_path):
        monitor = _make_monitor(tmp_path, websocket_client=None, ide_flash=False)
        with patch('esp_idf_monitor.idf_monitor.run_make') as local_make:
            monitor.run_make('flash', env_extra={'IDF_FLASH_FULL': '1'})
        local_make.assert_called_once()
        assert local_make.call_args.kwargs.get('env_extra') == {'IDF_FLASH_FULL': '1'}


def test_post_handoff_reopen_retries_instead_of_exiting():
    """After a previous successful open, a busy port must reconnect rather
    than applying the one-shot cold-start exit used for ``--open-port-attempts=1``,
    and print the same lines as after a disconnect.
    """
    serial_instance = MagicMock()
    serial_instance.is_open = False
    serial_instance.portstr = '/dev/ttyUSB0'
    serial_instance.baudrate = 115200
    with patch('esp_idf_monitor.base.serial_reader.Reset'):
        reader = SerialReader(serial_instance, MagicMock(), reset=False, open_port_attempts=1, target='esp32')

    reader._had_open_session = True
    reader._thread = object()
    opens = {'n': 0}

    def open_serial(*, reset):
        opens['n'] += 1
        if opens['n'] == 1:
            raise serial.SerialException('busy')
        reader.serial.is_open = True

    reader.open_serial = MagicMock(side_effect=open_serial)
    reader.close_serial = MagicMock()

    def stop_after_open(*_args, **_kwargs):
        reader._thread = None
        return b''

    reader.serial.read = MagicMock(side_effect=stop_after_open)
    reader.serial.in_waiting = 0

    with patch('esp_idf_monitor.base.serial_reader.time.sleep'):
        with patch('esp_idf_monitor.base.serial_reader.log.err') as log_err:
            with patch('esp_idf_monitor.base.serial_reader.log.print') as log_print:
                reader.run()

    assert opens['n'] == 2
    assert reader._had_open_session is True
    log_err.assert_called_once_with('busy')
    assert call('Waiting for the device to reconnect', end='', style='yellow') in log_print.call_args_list
