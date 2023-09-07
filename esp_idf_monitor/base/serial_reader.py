# SPDX-FileCopyrightText: 2015-2021 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0

import queue  # noqa: F401
import subprocess  # noqa: F401
import sys
import time

import serial
from serial.tools import list_ports

from .constants import (ASYNC_CLOSING_WAIT_NONE, CHECK_ALIVE_FLAG_TIMEOUT,
                        MINIMAL_EN_LOW_DELAY, RECONNECT_DELAY, TAG_SERIAL)
from .output_helpers import red_print, yellow_print
from .stoppable_thread import StoppableThread


class Reader(StoppableThread):
    """ Output Reader base class """


class SerialReader(Reader):
    """ Read serial data from the serial port and push to the
    event queue, until stopped.
    """

    def __init__(self, serial_instance, event_queue, reset):
        #  type: (serial.Serial, queue.Queue, bool) -> None
        super(SerialReader, self).__init__()
        self.baud = serial_instance.baudrate
        self.serial = serial_instance
        self.event_queue = event_queue
        self.gdb_exit = False
        self.reset = reset
        if not hasattr(self.serial, 'cancel_read'):
            # enable timeout for checking alive flag,
            # if cancel_read not available
            self.serial.timeout = CHECK_ALIVE_FLAG_TIMEOUT

    def run(self):
        #  type: () -> None
        if not self.serial.is_open:
            self.serial.baudrate = self.baud
            # We can come to this thread at startup or from external application line GDB.
            # If we come from GDB we would like to continue to run without reset.

            high = False
            low = True

            self.serial.dtr = low      # Non reset state
            self.serial.rts = high     # IO0=HIGH
            self.serial.dtr = self.serial.dtr   # usbser.sys workaround
            # Current state not reset the target!
            try:
                self.serial.open()
            except serial.serialutil.SerialException:
                # if connection to port fails suggest other available ports
                port_list = '\n'.join([p.device for p in list_ports.comports()])
                yellow_print(f'Connection to {self.serial.portstr} failed. Available ports:\n{port_list}')
                return
            if not self.gdb_exit and self.reset:
                self.serial.dtr = high     # Set dtr to reset state (affected by rts)
                self.serial.rts = low      # Set rts/dtr to the reset state
                self.serial.dtr = self.serial.dtr   # usbser.sys workaround

                # Add a delay to meet the requirements of minimal EN low time (2ms for ESP32-C3)
                time.sleep(MINIMAL_EN_LOW_DELAY)
            elif not self.reset:
                self.serial.setDTR(high)  # IO0=HIGH, default state
            self.gdb_exit = False
            self.serial.rts = high             # Set rts/dtr to the working state
            self.serial.dtr = self.serial.dtr   # usbser.sys workaround
        try:
            while self.alive:
                try:
                    data = self.serial.read(self.serial.in_waiting or 1)
                except (serial.serialutil.SerialException, IOError) as e:
                    data = b''
                    # self.serial.open() was successful before, therefore, this is an issue related to
                    # the disappearance of the device
                    red_print(e.strerror)
                    yellow_print('Waiting for the device to reconnect', newline='')
                    self.close_serial()
                    while self.alive:  # so that exiting monitor works while waiting
                        try:
                            time.sleep(RECONNECT_DELAY)
                            if not self.reset:
                                self.serial.dtr = low      # Non reset state
                                self.serial.rts = high     # IO0=HIGH
                                self.serial.dtr = self.serial.dtr   # usbser.sys workaround
                            self.serial.open()
                            break  # device connected
                        except serial.serialutil.SerialException:
                            yellow_print('.', newline='')
                            sys.stderr.flush()
                    yellow_print('')  # go to new line
                if data:
                    self.event_queue.put((TAG_SERIAL, data), False)
        finally:
            self.close_serial()

    def close_serial(self):
        if sys.platform == 'linux':
            # Avoid waiting for 30 seconds before closing the serial connection
            self._disable_closing_wait_or_discard_data()
        self.serial.close()

    def _disable_closing_wait_or_discard_data(self):  # type: () -> None
        import fcntl
        import struct
        import termios

        # `serial_struct` format based on linux kernel source:
        # https://github.com/torvalds/linux/blob/25aa0bebba72b318e71fe205bfd1236550cc9534/include/uapi/linux/serial.h#L19
        struct_format = 'iiIiiiiiHcciHHPHIL'
        buf = bytes(struct.calcsize(struct_format))

        # get serial_struct
        buf = fcntl.ioctl(self.serial.fd, termios.TIOCGSERIAL, buf)
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
        except PermissionError:
            # Discard written but not yet transmitted data
            termios.tcflush(self.serial.fd, termios.TCOFLUSH)

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

        self._stdout = iter(self.proc.stdout.readline, b'')  # type: ignore

    def run(self):  # type: () -> None
        try:
            while self.alive:
                for line in self._stdout:
                    if line:
                        self.event_queue.put((TAG_SERIAL, line), False)
        finally:
            self.proc.terminate()

    def _cancel(self):  # type: () -> None
        self.proc.terminate()
