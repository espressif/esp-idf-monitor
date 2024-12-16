# SPDX-FileCopyrightText: 2015-2024 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import queue  # noqa: F401
import subprocess  # noqa: F401
import sys
import time

import serial
from serial.tools import list_ports

from .constants import (ASYNC_CLOSING_WAIT_NONE, CHECK_ALIVE_FLAG_TIMEOUT,
                        FILTERED_PORTS, HIGH, LOW, RECONNECT_DELAY, TAG_SERIAL)
from .output_helpers import error_print, note_print, yellow_print
from .reset import Reset
from .stoppable_thread import StoppableThread


class Reader(StoppableThread):
    """ Output Reader base class """


class SerialReader(Reader):
    """ Read serial data from the serial port and push to the
    event queue, until stopped.
    """

    def __init__(self, serial_instance, event_queue, reset, open_port_attempts, target):
        #  type: (serial.Serial, queue.Queue, bool, int, str) -> None
        super(SerialReader, self).__init__()
        self.baud = serial_instance.baudrate
        self.serial = serial_instance
        self.event_queue = event_queue
        self.gdb_exit = False
        self.reset = reset
        self.open_port_attempts = open_port_attempts
        self.reset_strategy = Reset(serial_instance, target)
        if not hasattr(self.serial, 'cancel_read'):
            # enable timeout for checking alive flag,
            # if cancel_read not available
            self.serial.timeout = CHECK_ALIVE_FLAG_TIMEOUT

    def run(self):
        #  type: () -> None
        if not self.serial.is_open:
            self.serial.baudrate = self.baud
            try:
                # We can come to this thread at startup or from external application line GDB.
                # If we come from GDB we would like to continue to run without reset.
                self.reset = not self.gdb_exit and self.reset
                self.open_serial(reset=self.reset)
                # Successfully connected, so any further reconnections should occur without a reset.
                self.reset = False
            except (serial.SerialException, IOError, OSError) as e:
                print(e)
                if self.open_port_attempts == 1:
                    # If the connection to the port fails and --open-port-attempts was not specified,
                    # recommend other available ports and exit.
                    port_list = '\n'.join(
                        [
                            p.device
                            for p in list_ports.comports()
                            if not p.device.endswith(FILTERED_PORTS)
                        ]
                    )
                    note_print(f'Connection to {self.serial.portstr} failed. Available ports:\n{port_list}')
                    return
            self.gdb_exit = False
        try:
            while self.alive:
                try:
                    if self.serial.is_open:
                        # in_waiting assumes the port is already open
                        data = self.serial.read(self.serial.in_waiting or 1)
                    else:
                        raise serial.PortNotOpenError
                except (serial.SerialException, IOError, OSError) as e:
                    data = b''
                    # self.serial.open() was successful before, therefore, this is an issue related to
                    # the disappearance of the device
                    error_print(str(e))
                    note_print('Waiting for the device to reconnect', newline='')
                    self.close_serial()
                    while self.alive:  # so that exiting monitor works while waiting
                        try:
                            time.sleep(RECONNECT_DELAY)
                            # reset on reconnect can be unexpected for wakeup from deepsleep using JTAG
                            self.open_serial(reset=self.reset)
                            self.reset = False
                            break  # device connected
                        except (serial.SerialException, IOError, OSError):
                            yellow_print('.', newline='')
                            sys.stderr.flush()

                    yellow_print('')  # go to new line
                if data:
                    self.event_queue.put((TAG_SERIAL, data), False)
        finally:
            self.close_serial()

    def open_serial(self, reset: bool) -> None:
        # set the DTR/RTS into LOW prior open
        self.reset_strategy._setRTS(LOW)
        self.reset_strategy._setDTR(LOW)

        self.serial.open()

        # set DTR/RTS into expected HIGH state, but set the RTS first to avoid reset
        self.reset_strategy._setRTS(HIGH)
        self.reset_strategy._setDTR(HIGH)
        if reset:
            self.reset_strategy.hard()

    def close_serial(self):
        # Avoid waiting for 30 seconds before closing the serial connection
        self._disable_closing_wait_or_discard_data()
        self.serial.close()

    def _disable_closing_wait_or_discard_data(self):  # type: () -> None
        # ignore setting closing wait for network ports such as RFC2217
        if sys.platform == 'linux' and hasattr(self.serial, 'fd') and self.serial.is_open:
            import fcntl
            import struct
            import termios

            # `serial_struct` format based on linux kernel source:
            # https://github.com/torvalds/linux/blob/25aa0bebba72b318e71fe205bfd1236550cc9534/include/uapi/linux/serial.h#L19
            struct_format = 'iiIiiiiiHcciHHPHIL'
            buf = bytes(struct.calcsize(struct_format))

            # get serial_struct
            try:
                buf = fcntl.ioctl(self.serial.fd, termios.TIOCGSERIAL, buf)
            except IOError:
                # port has been disconnected
                return
            serial_struct = list(struct.unpack(struct_format, buf))

            # set `closing_wait` - amount of time, in hundredths of a second, that the kernel should wait before closing port
            # `closing_wait` is 13th (indexing from 0) variable in `serial_struct`, for reference see struct_format var
            if serial_struct[12] == ASYNC_CLOSING_WAIT_NONE:
                return

            serial_struct[12] = ASYNC_CLOSING_WAIT_NONE

            # set serial_struct
            buf = struct.pack(struct_format, *serial_struct)
            try:
                fcntl.ioctl(self.serial.fd, termios.TIOCSSERIAL, buf)
            except OSError:
                # Discard written but not yet transmitted data
                termios.tcflush(self.serial.fd, termios.TCOFLUSH)
        else:
            pass

    def _cancel(self):
        #  type: () -> None
        if hasattr(self.serial, 'cancel_read'):
            try:
                self.serial.cancel_read()
            except Exception:  # noqa
                pass


class LinuxReader(Reader):
    """ Read data from the subprocess that runs runnable and push to the
    event queue, until stopped.
    """

    def __init__(self, process, event_queue):
        #  type: (subprocess.Popen, queue.Queue) -> None
        super().__init__()
        self.proc = process
        self.event_queue = event_queue

    def run(self):  # type: () -> None
        try:
            while self.alive:
                c = self.proc.stdout.read(1)  # type: ignore
                if c:
                    self.event_queue.put((TAG_SERIAL, c), False)
        finally:
            self.proc.terminate()

    def _cancel(self):  # type: () -> None
        self.proc.terminate()
