# SPDX-FileCopyrightText: 2015-2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import json
import time

from .output_helpers import error_print, note_print

try:
    import websocket
except ImportError:
    # This is needed for IDE integration only.
    pass


class WebSocketClient(object):
    """
    WebSocket client used to advertise debug events to WebSocket server by sending and receiving JSON-serialized
    dictionaries.

    Advertisement of debug event:
    {'event': 'gdb_stub', 'port': '/dev/ttyUSB1', 'prog': 'build/elf_file'} for GDB Stub, or
    {'event': 'coredump', 'file': '/tmp/xy', 'prog': 'build/elf_file'} for coredump,
    where 'port' is the port for the connected device, 'prog' is the full path to the ELF file and 'file' is the
    generated coredump file.

    Expected end of external debugging:
    {'event': 'debug_finished'}
    """

    RETRIES = 3
    CONNECTION_RETRY_DELAY = 1

    def __init__(self, url):  # type: (str) -> None
        self.url = url
        self._connect()

    def _connect(self):  # type: () -> None
        """
        Connect to WebSocket server at url
        """

        self.close()
        for _ in range(self.RETRIES):
            try:
                self.ws = websocket.create_connection(self.url)
                break  # success
            except NameError:
                raise RuntimeError('Please install the websocket_client package for IDE integration!')
            except Exception as e:  # noqa
                error_print('WebSocket connection error: {e}')
            time.sleep(self.CONNECTION_RETRY_DELAY)
        else:
            raise RuntimeError('Cannot connect to WebSocket server')

    def close(self):  # type: () -> None
        try:
            self.ws.close()
        except AttributeError:
            # Not yet connected
            pass
        except Exception as e:  # noqa
            error_print('WebSocket close error: {e}')

    def send(self, payload_dict):  # type: (dict) -> None
        """
        Serialize payload_dict in JSON format and send it to the server
        """
        for _ in range(self.RETRIES):
            try:
                self.ws.send(json.dumps(payload_dict))
                note_print(f'WebSocket sent: {payload_dict}')
                break
            except Exception as e:  # noqa
                error_print(f'WebSocket send error: {e}')
                self._connect()
        else:
            raise RuntimeError('Cannot send to WebSocket server')

    def wait(self, expect_iterable):  # type: (list) -> None
        """
        Wait until a dictionary in JSON format is received from the server with all (key, value) tuples from
        expect_iterable.
        """
        for _ in range(self.RETRIES):
            try:
                r = self.ws.recv()
            except Exception as e:
                error_print(f'WebSocket receive error: {e}')
                self._connect()
                continue
            obj = json.loads(r)
            if all([k in obj and obj[k] == v for k, v in expect_iterable]):
                note_print(f'WebSocket received: {obj}')
                break
            error_print(f'WebSocket expected: {dict(expect_iterable)}, received: {obj}')
        else:
            raise RuntimeError('Cannot receive from WebSocket server')
