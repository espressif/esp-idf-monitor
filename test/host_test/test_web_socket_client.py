# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Host-side tests for ``esp_idf_monitor.base.web_socket_client.WebSocketClient``.

The class dispatches between two backends:

* `_PylibBackend` — preferred, available when ``esp-pylib`` is importable
  (Python >= 3.8).
* `_LegacyBackend` — fallback used on Python 3.7 (or when esp-pylib is
  not installed). Uses the ``websocket-client`` package.

These tests stub both backends so they run without any network or extra
package installs, and verify:

* the dispatch picks the right backend per Python version,
* the ``send`` / ``wait`` adapters preserve the legacy ``WebSocket sent: ...``
  / ``WebSocket received: ...`` log lines that the IDE-integration target test
  greps for,
* the pylib adapter correctly translates the dict-payload API into
  ``send_event`` / ``wait_for_event`` calls and cleans up the connection.
"""

import importlib
import sys
from unittest.mock import MagicMock

import pytest

from esp_idf_monitor.base.monitor_log import install_monitor_log


def _reload_module(monkeypatch_pylib=None, monkeypatch_websocket=None):
    """Reload web_socket_client with explicit `_pylib_ws` / `websocket` stubs.

    The module captures both at import time, so swapping them via
    ``patch.dict(sys.modules, ...)`` requires a reload to take effect.

    Note: ``from esp_pylib import ws`` resolves ``ws`` as an attribute of the
    parent ``esp_pylib`` package, so the mock must be installed as an
    attribute of the parent mock as well — not just in ``sys.modules``.
    """
    import esp_idf_monitor.base.web_socket_client as wsc

    if monkeypatch_pylib is not None:
        parent = MagicMock()
        parent.ws = monkeypatch_pylib
        sys.modules['esp_pylib'] = parent
        sys.modules['esp_pylib.ws'] = monkeypatch_pylib
    else:
        # Force ImportError on `from esp_pylib import ws` even when esp-pylib
        # is installed in the test environment — `None` in sys.modules tells
        # the import machinery the module is intentionally absent.
        sys.modules['esp_pylib'] = None  # type: ignore[assignment]
        sys.modules['esp_pylib.ws'] = None  # type: ignore[assignment]

    if monkeypatch_websocket is not None:
        sys.modules['websocket'] = monkeypatch_websocket
    else:
        sys.modules['websocket'] = None  # type: ignore[assignment]

    return importlib.reload(wsc)


@pytest.fixture
def restore_module():
    """Restore the original module state after each test."""
    yield
    # Drop our ``None`` / mock placeholders so the next reload re-imports the real
    # esp-pylib (and real websocket-client, if installed) from disk.
    for name in ('esp_pylib', 'esp_pylib.ws', 'websocket'):
        if name in sys.modules and sys.modules[name] is not None:
            mod = sys.modules[name]
            if isinstance(mod, MagicMock):
                sys.modules.pop(name, None)
        elif sys.modules.get(name) is None:
            sys.modules.pop(name, None)
    import esp_idf_monitor.base.web_socket_client as wsc

    importlib.reload(wsc)


class TestDispatch:
    @pytest.mark.usefixtures('restore_module')
    def test_prefers_pylib_when_available(self):
        fake_pylib = MagicMock()
        fake_pylib.set_ws_url = MagicMock()
        fake_pylib.ensure_connected = MagicMock()
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        if sys.version_info < (3, 8):
            pytest.skip('Pylib backend is gated on Python >= 3.8')

        client = wsc.WebSocketClient('ws://localhost:1234')
        assert isinstance(client._impl, wsc._PylibBackend)
        fake_pylib.set_ws_url.assert_called_once_with('ws://localhost:1234')
        fake_pylib.ensure_connected.assert_called_once()

    @pytest.mark.usefixtures('restore_module')
    def test_falls_back_to_legacy_when_pylib_missing(self):
        fake_ws_module = MagicMock()
        fake_ws_module.create_connection.return_value = MagicMock()
        wsc = _reload_module(monkeypatch_pylib=None, monkeypatch_websocket=fake_ws_module)

        client = wsc.WebSocketClient('ws://localhost:1234')
        assert isinstance(client._impl, wsc._LegacyBackend)
        fake_ws_module.create_connection.assert_called_once_with('ws://localhost:1234')

    @pytest.mark.usefixtures('restore_module')
    def test_falls_back_to_legacy_when_pylib_lacks_set_ws_url(self):
        """An older esp-pylib install without the IDE module must trigger the legacy fallback,
        not crash with AttributeError."""
        fake_pylib = MagicMock(spec=['some_other_attr'])
        fake_ws_module = MagicMock()
        fake_ws_module.create_connection.return_value = MagicMock()
        wsc = _reload_module(monkeypatch_pylib=fake_pylib, monkeypatch_websocket=fake_ws_module)

        client = wsc.WebSocketClient('ws://localhost:1234')
        assert isinstance(client._impl, wsc._LegacyBackend)


@pytest.mark.skipif(sys.version_info < (3, 8), reason='pylib backend requires Python >= 3.8')
class TestPylibBackend:
    @pytest.mark.usefixtures('restore_module')
    def test_send_translates_event_payload(self):
        """Spreading the dict (`send_event(**payload_dict)`) lets Python's call
        binding route ``payload_dict['event']`` to the named ``event`` parameter
        and the rest into ``**kwargs`` — no manual pop required. The call shape
        is therefore all-kwargs, which is functionally equivalent to a positional
        ``event``."""
        fake_pylib = MagicMock()
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        client = wsc.WebSocketClient('ws://localhost:9')
        client.send({'event': 'gdb_stub', 'port': '/dev/ttyUSB0', 'prog': '/x.elf'})

        fake_pylib.send_event.assert_called_once_with(event='gdb_stub', port='/dev/ttyUSB0', prog='/x.elf')

    @pytest.mark.usefixtures('restore_module')
    def test_send_without_event_key_propagates_typeerror(self):
        """A payload without 'event' is a programmer error: spreading the dict
        into ``send_event(event, **kwargs)`` raises TypeError naturally, which
        is enough signal for what is a static call-site bug. We deliberately
        do not catch and re-wrap it — the original message is clearer."""
        # Use a real function so Python's call-binding produces the canonical
        # TypeError; a MagicMock would happily accept any kwargs.
        fake_pylib = MagicMock()
        fake_pylib.send_event.side_effect = lambda event, **kwargs: None
        # Replace MagicMock's accept-anything send_event with a real signature.

        def real_send_event(event, **kwargs):
            return None

        fake_pylib.send_event = real_send_event
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        client = wsc.WebSocketClient('ws://localhost:9')
        with pytest.raises(TypeError, match='event'):
            client.send({'port': '/dev/ttyUSB0'})

    @pytest.mark.usefixtures('restore_module')
    def test_wait_translates_to_wait_for_event(self):
        fake_pylib = MagicMock()
        fake_pylib.wait_for_event.return_value = {'event': 'debug_finished'}
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        client = wsc.WebSocketClient('ws://localhost:9')
        client.wait([('event', 'debug_finished')])

        fake_pylib.wait_for_event.assert_called_once_with('debug_finished')

    @pytest.mark.usefixtures('restore_module')
    def test_wait_rejects_multi_key_expectations(self):
        """Current callers only ever wait on a single ('event', '<name>') tuple.
        A multi-key expectation indicates a protocol change that needs explicit
        modelling, not silent best-effort matching."""
        from esp_pylib.errors import FatalError

        fake_pylib = MagicMock()
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        client = wsc.WebSocketClient('ws://localhost:9')
        with pytest.raises(FatalError, match='Unsupported WebSocket wait expectation'):
            client.wait([('event', 'debug_finished'), ('extra', 'value')])

    @pytest.mark.usefixtures('restore_module')
    def test_close_delegates_to_pylib(self):
        fake_pylib = MagicMock()
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        client = wsc.WebSocketClient('ws://localhost:9')
        client.close()

        fake_pylib.close.assert_called_once()

    @pytest.mark.usefixtures('restore_module')
    def test_constructor_failure_surfaces_pylib_fatal_error(self):
        """The adapter intentionally does not wrap esp-pylib's exceptions: each one
        already carries a specific message ('WebSocket not configured', 'Cannot connect',
        'websockets ... esp-pylib[ide]', etc.) and a generic re-raise would lose that
        signal. This test pins the contract that pylib's exception bubbles up untouched."""
        from esp_pylib.errors import FatalError

        fake_pylib = MagicMock()
        fake_pylib.ensure_connected.side_effect = FatalError('Cannot connect to WebSocket server')
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        with pytest.raises(FatalError, match='Cannot connect'):
            wsc.WebSocketClient('ws://localhost:9')

    @pytest.mark.usefixtures('restore_module')
    def test_send_logs_using_legacy_format(self, capsys):
        """Integration test ``pytest_monitor_ide_integration.py`` greps for the literal
        ``WebSocket sent: {...}`` and ``WebSocket received: {...}`` lines, so the pylib
        adapter must keep emitting them through ``log.note``."""
        # Match production: ``main()`` installs ``MonitorLog``, which routes
        # ``log.note`` to stderr.
        install_monitor_log()
        fake_pylib = MagicMock()
        fake_pylib.wait_for_event.return_value = {'event': 'debug_finished'}
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        client = wsc.WebSocketClient('ws://localhost:9')
        client.send({'event': 'gdb_stub', 'port': '/dev/ttyUSB0'})
        client.wait([('event', 'debug_finished')])

        # `log.note` writes to stderr.
        err = capsys.readouterr().err
        assert "WebSocket sent: {'event': 'gdb_stub'" in err
        assert "WebSocket received: {'event': 'debug_finished'}" in err

    @pytest.mark.usefixtures('restore_module')
    def test_roundtrip_emits_shared_log_lines(self, capsys):
        install_monitor_log()
        fake_pylib = MagicMock()
        fake_pylib.wait_for_event.return_value = {'event': 'debug_finished'}
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        client = wsc.WebSocketClient('ws://localhost:9')
        result = client.roundtrip(
            {'event': 'gdb_stub', 'port': '/dev/ttyUSB0', 'prog': 'app.elf'},
            'debug_finished',
        )

        assert result == {'event': 'debug_finished'}
        err = capsys.readouterr().err
        assert 'Communicating through WebSocket' in err
        assert 'Waiting for debug finished event' in err
        assert 'Communications through WebSocket is finished' in err
        fake_pylib.send_event.assert_called_once_with(event='gdb_stub', port='/dev/ttyUSB0', prog='app.elf')

    @pytest.mark.usefixtures('restore_module')
    def test_roundtrip_interrupt_after_send_is_wait_interrupted(self):
        install_monitor_log()
        fake_pylib = MagicMock()
        fake_pylib.wait_for_event.side_effect = KeyboardInterrupt
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        client = wsc.WebSocketClient('ws://localhost:9')
        with pytest.raises(wsc.WebSocketWaitInterrupted):
            client.roundtrip({'event': 'gdb_stub'}, 'debug_finished')

    @pytest.mark.usefixtures('restore_module')
    def test_wait_returns_received_payload(self):
        install_monitor_log()
        fake_pylib = MagicMock()
        fake_pylib.wait_for_event.return_value = {'event': 'flash_finished', 'success': False}
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        client = wsc.WebSocketClient('ws://localhost:9')
        result = client.wait([('event', 'flash_finished')])
        assert result == {'event': 'flash_finished', 'success': False}

    @pytest.mark.usefixtures('restore_module')
    def test_roundtrip_interrupt_before_send_stays_keyboard_interrupt(self, monkeypatch):
        install_monitor_log()
        fake_pylib = MagicMock()
        wsc = _reload_module(monkeypatch_pylib=fake_pylib)

        def note(msg):
            if msg == 'Communicating through WebSocket':
                raise KeyboardInterrupt

        monkeypatch.setattr(wsc.log, 'note', note)
        client = wsc.WebSocketClient('ws://localhost:9')
        with pytest.raises(KeyboardInterrupt) as exc_info:
            client.roundtrip({'event': 'gdb_stub'}, 'debug_finished')
        assert not isinstance(exc_info.value, wsc.WebSocketWaitInterrupted)


class TestLegacyBackend:
    @pytest.mark.usefixtures('restore_module')
    def test_init_raises_when_websocket_missing(self):
        """Both backends raise something a caller can catch as ``RuntimeError``:
        the legacy backend raises plain ``RuntimeError`` directly, and the pylib
        backend raises ``esp_pylib.errors.FatalError`` (itself a ``RuntimeError``
        subclass). So ``except RuntimeError`` works regardless of backend."""
        wsc = _reload_module(monkeypatch_pylib=None, monkeypatch_websocket=None)
        with pytest.raises(RuntimeError, match='websocket_client'):
            wsc._LegacyBackend('ws://localhost:9')

    @pytest.mark.usefixtures('restore_module')
    def test_send_emits_legacy_log_line(self, capsys):
        install_monitor_log()
        fake_ws_module = MagicMock()
        fake_conn = MagicMock()
        fake_ws_module.create_connection.return_value = fake_conn
        wsc = _reload_module(monkeypatch_pylib=None, monkeypatch_websocket=fake_ws_module)

        client = wsc._LegacyBackend('ws://localhost:9')
        client.send({'event': 'gdb_stub', 'port': '/dev/ttyUSB0'})

        err = capsys.readouterr().err
        assert "WebSocket sent: {'event': 'gdb_stub'" in err
        fake_conn.send.assert_called_once()
