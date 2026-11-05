# SPDX-FileCopyrightText: 2015-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""WebSocket client used to advertise debug events to an IDE.

Dispatches between two backends:

* `_PylibBackend` — preferred. Uses `esp_pylib.ws` for the
  underlying WebSocket I/O. Available wherever ``esp-pylib[ide]`` is
  installed (Python >= 3.8).
* `_LegacyBackend` — fallback. Uses the ``websocket-client``
  package and is kept for Python 3.7, where ``esp-pylib[ide]`` is not
  available because its ``websockets`` dependency requires Python >= 3.8.

The public `WebSocketClient` keeps its historical
``send(payload_dict)`` / ``wait(expect_iterable)`` / ``close()`` API and
adds ``roundtrip(payload_dict, wait_event)``, which sends an event and
waits for the IDE reply. Both backends emit
the same ``WebSocket sent: ...`` / ``WebSocket received: ...`` log lines
the IDE integration target test (``pytest_monitor_ide_integration.py``)
greps for.

Wire format: the pylib backend sends
``{"type": "event", "event": "<name>", ...}`` via `esp_pylib.ws.send_event`.
The legacy backend serializes the payload dict as-is, so its messages carry
no ``type`` key.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any
from typing import Iterable

from esp_pylib.errors import FatalError
from esp_pylib.logger import log

# Imported as a module so tests can swap ``esp_pylib.ws`` in ``sys.modules``
# and re-import. ``set_ws_url`` membership is the gate for picking the pylib
# backend — older esp-pylib installs (or wholly missing installs) will not
# expose it, and the legacy backend takes over.
try:  # pragma: no cover - exercised via reload in tests
    from esp_pylib import ws as _pylib_ws  # type: ignore[import]
except ImportError:  # pragma: no cover
    _pylib_ws = None  # type: ignore[assignment]

# Imported the same way for symmetry; tests force both to ``None`` to
# exercise the "no backend at all" error path.
try:  # pragma: no cover
    import websocket  # type: ignore[import]
except ImportError:  # pragma: no cover
    websocket = None  # type: ignore[assignment]


def _pylib_available() -> bool:
    """Return ``True`` when the pylib backend can be constructed.

    Gated on three things: the import succeeded, the IDE-side surface
    (``set_ws_url``) is present, and Python is >= 3.8. The Python check
    is duplicated because the websockets dependency does not install on
    3.7 even when ``esp-pylib`` itself does; an install with only the
    base extras gives us ``set_ws_url`` but no working transport, which
    would surface at connect time as a confusing
    ``"esp-pylib[ide]"`` FatalError instead of falling through cleanly.
    """
    if _pylib_ws is None:
        return False
    if not hasattr(_pylib_ws, 'set_ws_url'):
        return False
    if sys.version_info < (3, 8):
        return False
    return True


class WebSocketWaitInterrupted(KeyboardInterrupt):
    """The request was sent, but waiting for the IDE was interrupted."""


class _PylibBackend:
    """Backend that delegates to `esp_pylib.ws`."""

    def __init__(self, url: str) -> None:
        # ``set_ws_url`` pins the URL for the duration of the session;
        # ``ensure_connected`` opens the socket immediately and raises a
        # descriptive ``FatalError`` if the URL is unset, ``websockets``
        # is missing, or the retries are exhausted. We propagate that
        # exception untouched: it already explains what went wrong, and
        # a generic wrapper would discard the diagnostic detail.
        assert _pylib_ws is not None  # _pylib_available() gated construction
        _pylib_ws.set_ws_url(url)
        _pylib_ws.ensure_connected()

    def send(self, payload_dict: dict[str, Any]) -> None:
        """Send an event payload.

        The dict is splatted into `esp_pylib.ws.send_event` so
        Python's call binding routes ``payload_dict['event']`` to the
        named ``event`` parameter and the rest into ``**kwargs``. A
        payload missing ``event`` therefore raises ``TypeError``
        naturally — a clearer signal of the static call-site bug than a
        re-wrapped exception would be.
        """
        assert _pylib_ws is not None
        _pylib_ws.send_event(**payload_dict)
        # Emit the "WebSocket sent: ..." line the IDE integration target
        # test greps for. The payload dict is logged as-is, not the
        # envelope produced by ``send_event``, so the output matches what
        # the call site passed in.
        log.note(f'WebSocket sent: {payload_dict}')

    def wait(self, expect_iterable: Iterable[tuple[str, Any]]) -> Any:
        """Wait for an event matching ``expect_iterable``.

        Current callers only ever pass a single-tuple iterable like
        ``[('event', 'debug_finished')]``; a multi-key expectation
        signals a protocol change that needs explicit modelling rather
        than silent best-effort matching. We raise
        `esp_pylib.errors.FatalError` to make that promotion an
        observable build failure.

        Returns the received payload dict.
        """
        # Materialise once so we can both validate length and dispatch.
        expectations = list(expect_iterable)
        assert _pylib_ws is not None
        if len(expectations) != 1 or expectations[0][0] != 'event':
            raise FatalError(f'Unsupported WebSocket wait expectation: {expectations!r}')
        event_name = expectations[0][1]
        result = _pylib_ws.wait_for_event(event_name)
        log.note(f'WebSocket received: {result}')
        return result

    def close(self) -> None:
        assert _pylib_ws is not None
        _pylib_ws.close()


class _LegacyBackend:
    """Fallback backend using the ``websocket-client`` package.

    Kept only for Python 3.7 (and any environment where esp-pylib's
    ``[ide]`` extra didn't install). Like the pylib backend, it raises
    `esp_pylib.errors.FatalError` when it cannot connect, send, or receive.
    """

    RETRIES = 3
    CONNECTION_RETRY_DELAY = 1

    def __init__(self, url: str) -> None:
        # Fail fast when the websocket-client package is unavailable.
        # ``websocket is None`` means the top-level import did not
        # succeed (either the package is not installed or tests have
        # explicitly stubbed it out). Reporting that condition before
        # the connect loop keeps the error message stable for callers
        # / tests that grep for ``"websocket_client"`` and avoids
        # surfacing a misleading "Cannot connect" after three retry
        # attempts against a no-op stub.
        if websocket is None:
            raise FatalError('Please install the websocket_client package for IDE integration!')
        self.url = url
        self._connect()

    def _connect(self) -> None:
        self.close()
        for _ in range(self.RETRIES):
            try:
                self.ws = websocket.create_connection(self.url)
                break
            except Exception as e:
                log.err(f'WebSocket connection error: {e}')
            time.sleep(self.CONNECTION_RETRY_DELAY)
        else:
            raise FatalError('Cannot connect to WebSocket server')

    def close(self) -> None:
        try:
            self.ws.close()
        except AttributeError:
            pass
        except Exception as e:
            log.err(f'WebSocket close error: {e}')

    def send(self, payload_dict: dict[str, Any]) -> None:
        for _ in range(self.RETRIES):
            try:
                self.ws.send(json.dumps(payload_dict))
                log.note(f'WebSocket sent: {payload_dict}')
                return
            except Exception as e:
                log.err(f'WebSocket send error: {e}')
                self._connect()
        raise FatalError('Cannot send to WebSocket server')

    def wait(self, expect_iterable: Iterable[tuple[str, Any]]) -> Any:
        expectations = list(expect_iterable)
        for _ in range(self.RETRIES):
            try:
                r = self.ws.recv()
            except Exception as e:
                log.err(f'WebSocket receive error: {e}')
                self._connect()
                continue
            obj = json.loads(r)
            if all(k in obj and obj[k] == v for k, v in expectations):
                log.note(f'WebSocket received: {obj}')
                return obj
            log.err(f'WebSocket expected: {dict(expectations)}, received: {obj}')
        raise FatalError('Cannot receive from WebSocket server')


class WebSocketClient:
    """Public WebSocket client used by the gdb / coredump / flash handlers.

    Dispatches to a pylib- or websocket-client-backed implementation
    based on what's available at construction time. Both backends share
    the same public API (``send``, ``wait``, ``close``) so call sites
    don't need to know which is active.

    Advertisement payloads:

    * ``{'event': 'gdb_stub', 'port': '/dev/ttyUSB1', 'prog': 'build/elf'}``
    * ``{'event': 'coredump', 'file': '/tmp/xy', 'prog': 'build/elf'}``
    * ``{'event': 'flash', 'port': '/dev/ttyUSB1', 'target': '<name>',
      'full': False}`` where ``target`` is one of ``flash``, ``app-flash``,
      ``encrypted-flash``, or ``encrypted-app-flash``. An encrypted full
      flash is sent as ``encrypted-flash`` with ``full: False`` (esptool
      forces a full flash for encrypted images).

    Expected close-out from the IDE:

    * ``{'event': 'debug_finished'}`` after gdb_stub / coredump
    * ``{'event': 'flash_finished', 'success': True}`` after flash
      (``success`` is optional; missing is treated as success)
    """

    _impl: Any  # _PylibBackend or _LegacyBackend; lazy union annotation

    def __init__(self, url: str) -> None:
        if _pylib_available():
            self._impl = _PylibBackend(url)
        else:
            self._impl = _LegacyBackend(url)

    def send(self, payload_dict: dict[str, Any]) -> None:
        self._impl.send(payload_dict)

    def wait(self, expect_iterable: Iterable[tuple[str, Any]]) -> Any:
        return self._impl.wait(expect_iterable)

    def roundtrip(self, payload_dict: dict[str, Any], wait_event: str) -> Any:
        """Send ``payload_dict`` and block until ``wait_event`` arrives.

        Emits the same ``Communicating through WebSocket`` /
        ``Waiting for ...`` / ``Communications through WebSocket is finished``
        log lines that the IDE integration target test greps for.
        Returns the received payload dict.

        Raises `WebSocketWaitInterrupted` on Ctrl+C once ``send()`` has
        started, so callers keep the serial port released until the IDE
        replies. Ctrl+C before ``send()`` stays a plain ``KeyboardInterrupt``:
        the IDE was never asked to reply.
        """
        log.note('Communicating through WebSocket')
        try:
            self.send(payload_dict)
            # Keep the historical phrasing for debug_finished so target tests
            # that expect "Waiting for debug finished event" keep matching.
            wait_label = wait_event.replace('_', ' ')
            log.note(f'Waiting for {wait_label} event')
            result = self.wait([('event', wait_event)])
        except KeyboardInterrupt as e:
            raise WebSocketWaitInterrupted from e
        log.note('Communications through WebSocket is finished')
        return result

    def close(self) -> None:
        self._impl.close()

    # Optional helper used by ``idf_monitor.main()`` to control the
    # pylib-side URL cache on exit. No-op for the legacy backend, which
    # has no global URL state.
    def shutdown(self) -> None:  # pragma: no cover - thin compatibility shim
        try:
            self.close()
        except Exception:
            pass
