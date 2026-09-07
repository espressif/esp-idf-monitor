#!/usr/bin/env python
#
# SPDX-FileCopyrightText: 2018-2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
import codecs
import datetime
import errno
import filecmp
import os
import queue
import random
import re
import socket
import subprocess
import sys
import threading
import time
from tempfile import NamedTemporaryFile
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from unittest.mock import patch

import pytest
from esp_pylib.logger import log

from esp_idf_monitor import __version__
from esp_idf_monitor.base.binlog import BinaryLog
from esp_idf_monitor.base.command_reader import CommandReader
from esp_idf_monitor.base.console_parser import ConsoleParser
from esp_idf_monitor.base.constants import CMD_APP_FLASH
from esp_idf_monitor.base.constants import CMD_ENTER_BOOT
from esp_idf_monitor.base.constants import CMD_FLASH_ALL
from esp_idf_monitor.base.constants import CMD_MAKE
from esp_idf_monitor.base.constants import CMD_OUTPUT_TOGGLE
from esp_idf_monitor.base.constants import CMD_RESET
from esp_idf_monitor.base.constants import CMD_STOP
from esp_idf_monitor.base.constants import CMD_TOGGLE_LOGGING
from esp_idf_monitor.base.constants import CMD_TOGGLE_TIMESTAMPS
from esp_idf_monitor.base.constants import EXIT_EXPECT_TIMEOUT
from esp_idf_monitor.base.constants import EXIT_SCRIPT_ERROR
from esp_idf_monitor.base.constants import TAG_CMD
from esp_idf_monitor.base.constants import TAG_KEY
from esp_idf_monitor.base.logger import Logger
from esp_idf_monitor.idf_monitor import Monitor

from .conftest import out_dir

if os.name != 'nt':
    import pty

HOST = '127.0.0.1'

IN_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'inputs')  # input files for tests
# unique marker fed over the serial socket to stop the monitor in command mode (matched by 'expect')
STOP_MARKER = 'ESP_IDF_MONITOR_STOP'


def on_timeout(process):
    if process.poll() is not None:
        # process has already ended
        return
    try:
        process.kill()
        pytest.fail('Monitor timed out')
    except OSError as e:
        if e.errno == errno.ESRCH:
            # ignores a possible race condition which can occur when the process exits between poll() and kill()
            pass
        else:
            raise


def filename_fix(input_filename: str) -> str:
    """Remove invalid characters from filename on Windows"""
    if os.name == 'nt':
        regex = re.compile(r'[\\/:*?\"<>|]')
        return regex.sub('', input_filename)
    return input_filename


class TestBaseClass:
    """Base class to define shared fixtures and methods"""

    master_fd: Optional[int]
    slave_fd: Optional[int]
    proc: subprocess.Popen

    def send_control(self, sequence: str):
        """Send a control sequence to monitor STDIN
        Note: Monitor needs to be running in interactive async mode (run_monitor_async)
        """
        if self.master_fd is None:
            raise ValueError('Master FD is not set')
        byte = b''
        for c in sequence:
            # convert letter to control code
            byte += bytes([ord(c) - ord('@')])
        os.write(self.master_fd, byte)

    def close_monitor_async(self, timeout: int = 5) -> Optional[int]:
        """Close monitor running in async mode and get the return code"""
        # close monitor
        self.send_control(']')

        ret: Optional[int] = None
        for _ in range(timeout):
            ret = self.proc.poll()
            if ret is not None:
                break
            time.sleep(1)
        else:
            pytest.fail(f'Monitor took longer than {timeout} seconds to exit')
        return ret

    def run_monitor_async(self, args: List[str] = [], custom_port: str = '') -> Tuple[str, str]:
        """Run the monitor asynchronously in interactive mode (stdin on a PTY).

        The monitor keeps running after this returns; drive it with
        send_control() and stop it with close_monitor_async(). Returns the
        stdout and stderr filenames.
        """
        cmd = [
            sys.executable,
            '-m',
            'esp_idf_monitor',
            '--port',
            custom_port if custom_port else f'socket://{HOST}:{self.port}?logging=debug',
        ] + args
        output_file = os.path.join(out_dir, filename_fix(self.test_name))
        if os.name == 'nt':
            self.master_fd, self.slave_fd = None, None
        else:
            # stdin needs to be connected to some pseudo-tty in docker image even when it is not used at all
            self.master_fd, self.slave_fd = pty.openpty()
        with open(f'{output_file}.out', 'w') as o_f, open(f'{output_file}.err', 'w') as e_f:
            self.proc = subprocess.Popen(cmd, stdin=self.slave_fd, stdout=o_f, stderr=e_f)
        # make sure monitor is running before sending data
        time.sleep(3 if os.name == 'nt' else 1)
        return f'{output_file}.out', f'{output_file}.err'

    def run_monitor_command_mode(
        self, args: List[str] = [], custom_port: str = '', stdin: int = subprocess.PIPE
    ) -> Tuple[str, str]:
        """Run the monitor in non-interactive command mode.

        stdin is not a TTY (a pipe or /dev/null), so the monitor reads
        line-based commands from it (CommandReader). The monitor keeps running
        after this returns. Returns the stdout and stderr filenames.
        """
        cmd = [
            sys.executable,
            '-m',
            'esp_idf_monitor',
            '--port',
            custom_port if custom_port else f'socket://{HOST}:{self.port}?logging=debug',
        ] + args
        # no PTY here: command mode is exactly the "stdin is not a TTY" path
        self.master_fd, self.slave_fd = None, None
        output_file = os.path.join(out_dir, filename_fix(self.test_name))
        with open(f'{output_file}.out', 'w') as o_f, open(f'{output_file}.err', 'w') as e_f:
            self.proc = subprocess.Popen(cmd, stdin=stdin, stdout=o_f, stderr=e_f)
        # let the monitor start up and finish the initial reset (which flushes
        # the serial input buffer) before the test sends serial data
        time.sleep(3 if os.name == 'nt' else 1)
        return f'{output_file}.out', f'{output_file}.err'

    def strip_marker(self, path: str) -> None:
        """Remove the stop-marker line (injected by run_monitor) from the output."""
        with open(path, 'rb') as f:
            lines = f.readlines()
        with open(path, 'wb') as f:
            f.writelines(line for line in lines if STOP_MARKER.encode() not in line)

    def run_monitor(
        self, args: List[str], input_file: str, custom_port: str = '', timeout: int = 60
    ) -> Tuple[str, str]:
        """Run IDF Monitor over an input file with a timeout.

        The monitor runs in non-interactive command mode. input_file is streamed
        over the serial socket, followed by a unique marker line; the 'expect'
        command waits for that marker, so the monitor exits only after all the
        input has been decoded and printed. The marker line is stripped from the
        captured stdout. Returns the stdout and stderr filenames.
        """
        out, err = self.run_monitor_command_mode(args, custom_port=custom_port)
        # create a timer
        monitor_watchdog = threading.Timer(timeout, on_timeout, [self.proc])
        monitor_watchdog.start()

        # make sure that monitor is running, else we will end in an infinite loop
        if self.proc.poll() is not None:
            pytest.fail('Monitor has already ended')
        assert self.proc.stdin is not None
        # arm 'expect' for the stop marker; the following EOF makes the monitor
        # exit once the marker is seen
        self.proc.stdin.write(f'expect {STOP_MARKER}\n'.encode())
        self.proc.stdin.close()
        # send input file content to socket
        clientsocket, _ = self.serversocket.accept()
        try:
            with open(os.path.join(IN_DIR, input_file), 'rb') as f:
                for chunk in iter(lambda: f.read(1024), b''):
                    clientsocket.sendall(chunk)
            # marker as the last serial line: once it is decoded, all of the
            # input has been processed
            clientsocket.sendall(f'{STOP_MARKER}\n'.encode())
            # wait for process to end
            while True:
                ret = self.proc.poll()
                if ret is not None:
                    break
                time.sleep(1)
            assert ret == 0
            monitor_watchdog.cancel()
        finally:
            clientsocket.close()
        # drop the marker line so the captured output matches the golden files
        self.strip_marker(out)
        return out, err

    def filecmp(self, file: str, expected_out: str) -> bool:
        """Compare two files, remove escape sequences from expected_out on Windows"""
        print(f'Comparing {file} with {expected_out}')
        try:
            if os.name == 'nt':
                # remove escape sequences form the file and create a new temp file
                ansi_regex = re.compile(r'\x1B\[\d+(;\d+){0,2}m')
                with NamedTemporaryFile(dir=IN_DIR, delete=False, mode='w+') as converted, open(
                    os.path.join(IN_DIR, expected_out)
                ) as input_file:
                    converted.writelines(ansi_regex.sub('', input_file.read()))
                    expected_out = converted.name
            return filecmp.cmp(file, os.path.join(IN_DIR, expected_out), shallow=False)
        finally:
            if os.name == 'nt':
                os.unlink(expected_out)

    def teardown_method(self):
        """Class teardown method to cleanup pseudo-tty used for STDIN"""
        if os.name != 'nt':
            try:
                os.close(self.slave_fd)
                os.close(self.master_fd)
            except Exception:
                pass

    @pytest.fixture(scope='module', autouse=True)
    def output_dir(self):
        """Make sure that output dir exists"""
        if not os.path.exists(out_dir):
            os.mkdir(out_dir)

    @pytest.fixture(autouse=True)
    def set_test_name(self, request):
        """Set test name for logging"""
        self.test_name = request.node.name

    @pytest.fixture(autouse=True)
    def get_port(self):
        """Create an new connection"""
        self.serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.serversocket.bind((HOST, 0))
        self.port = self.serversocket.getsockname()[1]
        self.serversocket.listen(5)
        yield
        try:
            self.serversocket.shutdown(socket.SHUT_RDWR)
            self.serversocket.close()
        except OSError:
            pass


class TestHost(TestBaseClass):
    @pytest.fixture
    def rfc2217(self):
        """Run RFC2217 server from esptool"""
        # create a new socket to find an empty port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('', 0))
        rfc2217_port = str(s.getsockname()[1])
        s.close()
        cmd = ' '.join(['esp_rfc2217_server.py', '-p', rfc2217_port, f'socket://{HOST}:{self.port}?logging=debug'])
        p = subprocess.Popen(cmd, shell=True)
        # wait for the server to start
        time.sleep(2)
        yield f'rfc2217://{HOST}:{rfc2217_port}?ign_set_control'
        p.terminate()

    # fmt: off
    @pytest.mark.parametrize(
        ['input_file', 'filter_pattern', 'expected_out', 'timeout'],
        [
            ('in1.txt', '',                                      'in1f1.txt',  60,),
            ('in1.txt', '*:V',                                   'in1f1.txt',  60,),
            ('in1.txt', 'hello_world',                           'in1f2.txt',  60,),
            ('in1.txt', '*:N',                                   'in1f3.txt',  60,),
            ('in2.txt', 'boot mdf_device_handle:I mesh:E vfs:I', 'in2f1.txt', 420,),
            ('in2.txt', 'vfs',                                   'in2f2.txt', 420,),
        ]
    )
    # fmt: on
    @pytest.mark.flaky(reruns=2)
    def test_print_filter(self, input_file: str, filter_pattern: str, expected_out: str, timeout: int):
        """Test monitor filtering feature"""
        args = ['--print_filter', filter_pattern]
        out, err = self.run_monitor(args, input_file, timeout=timeout)
        with open(err) as f_err:
            stderr = f_err.read()
            assert f"Expect pattern '{STOP_MARKER}' matched" in stderr
        assert self.filecmp(out, expected_out)

    def test_auto_color(self):
        """Test monitor auto-coloring feature"""
        # run monitor on empty input
        out, err = self.run_monitor([], 'color.txt')
        with open(err) as f_err:
            stderr = f_err.read()
            assert f"Expect pattern '{STOP_MARKER}' matched" in stderr
        assert self.filecmp(out, 'color_out.txt')

    @pytest.mark.skipif(os.name == 'nt', reason='Linux/MacOS only')
    def test_auto_color_advanced(self):
        """Test monitor auto-coloring feature with mixed line endings and delay in the middle of line"""
        # run monitor on empty input
        out, err = self.run_monitor_async()
        clientsocket, _ = self.serversocket.accept()
        try:
            clientsocket.send(b'I (1234) start of the line, ')
            time.sleep(1)  # wait for message to be processed
            clientsocket.send(b'continue on the next line\n')
            clientsocket.send(b'W (1234) mixed line endings\r\n')
            time.sleep(0.5)
            self.send_control('TI')  # toggle timestamps
            clientsocket.send(b'E (1234) error log with a timestamp\n')
            time.sleep(1)  # wait for messages to be processed
            self.send_control(']')  # close monitor
            time.sleep(1)
            assert self.close_monitor_async() == 0
        finally:
            clientsocket.close()
        with open(out) as f_out:
            output = f_out.read()
        assert '\033[0;32mI (1234) start of the line, continue on the next line\033[0m\n' in output
        assert '\033[0;33mW (1234) mixed line endings\033[0m\n' in output
        regex = re.compile(
            r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \033\[1;31mE \(1234\) error log with a timestamp\033\[0m\n'
        )
        assert regex.search(output) is not None

    @pytest.mark.skipif(os.name == 'nt', reason='Linux/MacOS only')
    def test_rfc2217(self, rfc2217: str):
        """Run monitor with RFC2217 port"""
        # run with no reset because it is not supported for socket ports
        input_file = 'in1.txt'
        out, err = self.run_monitor(['--no-reset'], input_file, custom_port=rfc2217)
        with open(err) as f:
            stderr = f.read()
        # check if monitor is running on RFC2217 port
        regex = re.compile(rf'--- esp-idf-monitor {re.escape(__version__)} on {re.escape(rfc2217)} \d*')
        assert regex.search(stderr) is not None
        assert 'Exception' not in stderr
        assert f"Expect pattern '{STOP_MARKER}' matched" in stderr
        assert self.filecmp(out, 'in1f1.txt')

    @pytest.mark.skipif(os.name == 'nt', reason='Linux/MacOS only')
    def test_upload_commands(self):
        """Run monitor with make flash and make flash-app commands"""
        # run monitor on empty input
        out, err = self.run_monitor_async()
        self.send_control('TJ')  # unknown command
        self.send_control('TA')  # make app-flash
        time.sleep(1)  # wait for make to run
        self.send_control('T')  # press any key to reset
        self.send_control('TF')  # make flash
        time.sleep(1)  # wait for make to run
        self.send_control('TX')
        assert self.close_monitor_async() == 0

        with open(err) as f_err:
            stderr = f_err.read()
        assert 'Running make app-flash...' in stderr  # Triggered by TA
        assert 'Running make flash...' in stderr  # TF
        assert 'Unknown menu character Ctrl+J' in stderr  # TJ

    @pytest.mark.skipif(os.name == 'nt', reason='Linux/MacOS only')
    def test_upload_all_command(self):
        """Run monitor with full flash (Ctrl-T Ctrl-E) command"""
        out, err = self.run_monitor_async()
        self.send_control('TE')  # make full flash
        time.sleep(1)  # wait for make to run
        self.send_control('TX')
        assert self.close_monitor_async() == 0

        with open(err) as f_err:
            stderr = f_err.read()
        assert 'Running make flash...' in stderr
        assert 'make flash -a' not in stderr

    @pytest.mark.skipif(os.name == 'nt', reason='Linux/MacOS only')
    def test_log(self):
        """Run monitor with logging enabled including the timestamps"""
        # run monitor on empty input
        out, err = self.run_monitor_async()
        monitor_watchdog = threading.Timer(60, on_timeout, [self.proc])
        monitor_watchdog.start()
        self.send_control('TL')  # toggle log file
        self.send_control('TI')  # toggle timestamps
        time.sleep(1)  # wait for commands to apply
        clientsocket, _ = self.serversocket.accept()
        input_file = 'in1.txt'
        try:
            with open(os.path.join(IN_DIR, input_file), 'rb') as f:
                for chunk in iter(lambda: f.read(1024), b''):
                    clientsocket.sendall(chunk)
            time.sleep(1)
            self.send_control('TL')  # close log file to make sure that output is written
            time.sleep(1)  # wait for command to apply
            assert self.close_monitor_async() == 0
            monitor_watchdog.cancel()
        finally:
            clientsocket.close()
        with open(err) as f_err:
            stderr = f_err.read()
        with open(out) as f_out:
            stdout = f_out.read()
        # check that timestamps are enabled
        date = datetime.datetime.now().strftime('%Y-%m-%d')
        assert date in stdout
        # make sure that logging was enabled
        regex = re.compile('Logging is enabled into file (.*\\.txt)')
        # compare log file with the output
        log_file = regex.search(stderr)
        assert log_file is not None
        self.filecmp(log_file.groups()[0], out_dir)

    @pytest.mark.skipif(os.name == 'nt', reason='Linux/MacOS only')
    def test_wrong_elf_file(self):
        """Run monitor with a path to non-existing ELF file"""
        # run monitor on empty input
        out, err = self.run_monitor_async(args=['non_existing.elf'])
        with open(err) as f_err:
            stderr = f_err.read()
        assert "ELF file 'non_existing.elf' does not exist" in stderr


class TestBinaryLogging(TestBaseClass):
    ELF_PATH = os.path.join(IN_DIR, 'log.elf')

    VALID_FRAME = b'\x02\x0c\x10\x00\x00\x85\x9c\x3f\x40\x09\x9c\x00\x00\x01\x03\xd6'
    VALID_FRAME_TEXT = b'I (259) example: >>> String Formatting Tests <<<\n'
    # VALID_FRAME2_TEXT is "I (259) example: |Hello_world|"
    VALID_FRAME2 = b'\x02\x0c\x14\x00\x00\x85\x94?@\t\x9c\x00\x00\x01\x03?@\nL\xa1'

    def test_binary_logging(self):
        args = [self.ELF_PATH, os.path.join(IN_DIR, 'bootloader.elf')]
        out, err = self.run_monitor(args, 'binlog', timeout=10)
        with open(err) as f_err:
            stderr = f_err.read()
            assert f"Expect pattern '{STOP_MARKER}' matched" in stderr

        ansi_regex = re.compile(r'\x1B\[\d+(;\d+){0,2}m')
        with open(out) as f_out, open(os.path.join(IN_DIR, 'binlog_out.txt')) as f_expected:
            for line_out, line_expected in zip(f_out, f_expected):
                if os.name == 'nt':
                    line_expected = ansi_regex.sub('', line_expected)
                    line_out = ansi_regex.sub('', line_out)
                line_out = line_out.strip()
                line_expected = line_expected.strip()
                assert line_out == line_expected, f'Mismatch: {line_out} != {line_expected}'

        if os.name == 'nt':
            # Windows test environment does not have toolchain installed
            return
        # Function addresses in the binary log output are decoded as for text logs
        with open(os.path.join(out_dir, 'test_binary_logging.err')) as f_expected:
            log_clean = ansi_regex.sub('', f_expected.read())
            print(log_clean)
            # Check for the full line with address, app_main, and main.c with line number
            assert re.search(r'0x[0-9a-f]+: app_main.* at .*main\.c.*:\d+', log_clean), (
                "Expected address, 'app_main', and 'main.c:<line>' in the output"
            )

    @pytest.fixture
    def invalid_binary_log(self):
        with NamedTemporaryFile(delete=False) as f:
            f.write(b'I (1) main: Starting\r\n')
            # Binary log detection trigger
            f.write(b'\x01')
            # Corrupted/invalid binary log data that would cause stuck behavior
            # The max length of the binary log frame is 1023 bytes so just to be sure we write more
            f.write(b'\x01' + random.randbytes(1024))
            # Text after binary log (should be processed normally)
            f.write(b'I (1000) main: Application started\r\n')
            # Add some valid binary log data frame from inputs/binlog
            f.write(self.VALID_FRAME)

        yield f.name
        os.unlink(f.name)

    def test_binary_log_invalid_data(self, invalid_binary_log: str):
        """Test the binary log with invalid data to make sure it is processed normally and not stuck"""
        args = [self.ELF_PATH]
        out, err = self.run_monitor(args, invalid_binary_log, timeout=15)
        print('Using binary log file: ', invalid_binary_log)
        with open(err) as f_err:
            stderr = f_err.read()
            assert f"Expect pattern '{STOP_MARKER}' matched" in stderr

        # Verify that monitor didn't get stuck and processed all data; ignore errors because we are using random data
        with open(out, errors='ignore') as f_out:
            output = f_out.read()
            # Should contain messages from both before and after invalid binary log processing
            assert 'I (1) main: Starting' in output
            assert 'I (1000) main: Application started' in output
            # Valid binary log data frame should be decoded
            assert 'I (259) example: >>> String Formatting Tests <<<' in output

    ### Unit tests for BinaryLog class ###

    def test_find_frames_resync_after_corruption(self):
        """Corrupted bytes between two valid frames: parser re-syncs and extracts both frames."""
        corrupt = b'\xff\xff\x01\x02'  # noise that could look like frame start
        data = self.VALID_FRAME + corrupt + self.VALID_FRAME2
        binlog = BinaryLog([self.ELF_PATH])
        frames, remaining, leaked_text = binlog.find_frames(data)
        assert len(frames) == 2
        assert frames[0] == self.VALID_FRAME
        assert frames[1] == self.VALID_FRAME2
        # Corrupt region is leaked (non-frame bytes between the two frames)
        assert corrupt in leaked_text or len(leaked_text) >= len(corrupt)
        assert remaining == b''

    def test_find_frames_crc_mismatch_skipped_recovery(self):
        """CRC mismatch: skip invalid frame and re-sync"""
        # Valid frame, then same frame with last byte flipped (bad CRC), then valid again
        bad_frame = self.VALID_FRAME[:-1] + bytes([self.VALID_FRAME[-1] ^ 0x01])
        data = self.VALID_FRAME + bad_frame + self.VALID_FRAME2
        binlog = BinaryLog([self.ELF_PATH])
        frames, remaining, leaked_text = binlog.find_frames(data)
        assert len(frames) == 2
        assert frames[0] == self.VALID_FRAME
        assert frames[1] == self.VALID_FRAME2
        assert remaining == b''
        assert leaked_text == bad_frame

    def test_find_frames_truncated_frame_no_crash(self):
        """Truncated frame (e.g. buffer cut mid-packet): no exception, partial not returned as frame."""
        truncated = self.VALID_FRAME[:10]  # too short to be a full frame
        binlog = BinaryLog([self.ELF_PATH])
        frames, remaining, _ = binlog.find_frames(truncated)
        assert len(frames) == 0
        # Either carried as remaining (if plausible) or leaked
        assert remaining == b'' or remaining == truncated

    def test_find_frames_noise_byte_like_marker_skipped(self):
        """Single 0x01 in middle of text: parser skips or recovers; no valid frame."""
        # 0x01 in the middle; next bytes are ASCII so control may be implausible and we skip
        text = b'ESP-ROM:esp32c3-api1-20210207\n'
        data = text[:12] + b'\x01' + text[12:]
        binlog = BinaryLog([self.ELF_PATH])
        frames, remaining, leaked_text = binlog.find_frames(data)
        assert len(frames) == 0
        # Parser did not crash; no valid frame; data is either leaked or carried as remaining
        assert len(leaked_text) > 0 or len(remaining) > 0
        assert leaked_text + remaining == data

    def test_find_frames_boot_log_leaked_after_last_frame(self):
        """Reset mid-binary-log: boot log after last valid frame must appear in leaked_text."""
        boot_log = b'ESP-ROM:esp32c3-api1-20210207\nBuild:Feb  7 2021\n'
        data = self.VALID_FRAME + boot_log
        binlog = BinaryLog([self.ELF_PATH])
        frames, remaining, leaked_text = binlog.find_frames(data)
        assert len(frames) == 1
        assert frames[0] == self.VALID_FRAME
        assert leaked_text == boot_log
        assert remaining == b''

    def test_convert_to_text_returns_leaked_text(self):
        boot_log = b'ESP-ROM:esp32c3-api1-20210207\nBuild:Feb  7 2021\n'
        data = self.VALID_FRAME + boot_log
        binlog = BinaryLog([self.ELF_PATH])
        messages, incomplete, leaked_text = binlog.convert_to_text(data)
        assert len(messages) == 1
        assert incomplete == b''
        assert leaked_text == boot_log
        # One decoded line from the valid frame
        assert self.VALID_FRAME_TEXT == messages[0]

    def test_find_frames_leaked_text_between_frames(self):
        """Non-frame bytes between two valid frames are collected as leaked_text."""
        between = b'noise-between\n'
        data = self.VALID_FRAME + between + self.VALID_FRAME2
        binlog = BinaryLog([self.ELF_PATH])
        frames, remaining, leaked_text = binlog.find_frames(data)
        assert len(frames) == 2
        assert between in leaked_text
        assert remaining == b''


@pytest.mark.skipif(os.name == 'nt', reason='Linux/MacOS only')
class TestConfig(TestBaseClass):
    def create_config(self, options: Dict[str, str], section='esp-idf-monitor', filename: str = 'config.cfg'):
        """Create a new config file in CWD"""
        config = [f'[{section}]\n']
        config.extend(f'{key} = {value}\n' for key, value in options.items())
        filename = os.path.join(os.getcwd(), filename)
        with open(filename, 'w') as f:
            f.writelines(config)
        return filename

    @pytest.mark.parametrize('filename', ['esp-idf-monitor.cfg', 'config.cfg', 'tox.ini'])
    def test_custom_config(self, filename: str):
        """Run monitor with custom and validate it is NOT case-sensitive"""
        # create custom config
        self.create_config({'chip_reset_key': 'J', 'toggle_log_key': 'k'}, filename=filename)
        try:
            # run monitor on empty input
            _, err = self.run_monitor_async()
            # use custom key
            self.send_control('TK')
            # make sure that command will be accepted before closing the monitor
            # chip input has priority and closing command is written to the chip input queue
            time.sleep(0.5)
            # show help command
            self.send_control('TH')
            assert self.close_monitor_async() == 0
        finally:
            os.unlink(filename)

        with open(err) as f_err:
            stderr = f_err.read()
        # make sure that custom config was applied and stderr has message about it
        assert f'Loaded custom configuration from {os.path.join(os.getcwd(), filename)}' in stderr
        # check that help command contains values from the config
        assert '---    Ctrl+J         Reset target board via RTS line' in stderr
        assert 'Ctrl+R' not in stderr
        assert '---    Ctrl+K         Toggle saving output into file' in stderr
        assert 'Ctrl+L' not in stderr
        # make sure that logging was enabled
        regex = re.compile('Logging is enabled into file (.*\\.txt)')
        log_file = regex.search(stderr)
        assert log_file is not None
        # make sure that log file was closed on monitor exit
        assert f'Logging is disabled and file {log_file.groups()[0]} has been closed' in stderr

    def test_skip_menu(self):
        """Run monitor with custom config to skip menu key"""
        # create custom config to skip menu
        self.create_config({'skip_menu_key': 'True'})
        # run monitor on empty input
        _, err = self.run_monitor_async()
        self.send_control('TH')  # show help command
        self.send_control('A')  # make app-flash (missing menu key)
        time.sleep(1)  # wait for make to run
        assert self.close_monitor_async() == 0

        with open(err) as f_err:
            stderr = f_err.read()
        # make sure that menu was skipped
        assert '--- Using the "skip_menu_key" option from a config file.' in stderr
        assert 'Running make app-flash...' in stderr  # Triggered by A

    def test_invalid_custom_config(self):
        # create custom config with unsupported value and unknown key
        self.create_config({'chip_reset_key': '.', 'foo': 'J'})
        # run monitor on empty input
        _, err = self.run_monitor_async()
        assert self.close_monitor_async() == 0

        with open(err) as f_err:
            stderr = f_err.read()
        # make sure that custom config was applied and stderr has message about it
        assert f'Loaded custom configuration from {os.path.join(os.getcwd(), "config.cfg")}' in stderr
        # check that stderr has message that config was not correct and fallback option works
        assert 'Ignoring unknown configuration options: foo' in stderr
        assert (
            "Unsupported configuration for key: '.', please use just the English alphabet "
            "characters (A-Z) and [,],\\,^,_. Using the default option 'R'." in stderr
        )

    def test_esptool_sequence(self):
        """Use custom reset sequence to reset into bootloader"""
        # create custom config with custom reset sequence
        self.create_config({'custom_reset_sequence': 'R1|W0.1|R0|D1'}, section='esptool')
        # run monitor
        _, err = self.run_monitor_async(args=['--no-reset'])
        # reset into bootloader
        self.send_control('TP')
        # wait for command to apply
        time.sleep(0.5)
        assert self.close_monitor_async() == 0

        with open(err) as f_err:
            stderr = f_err.read()
        msg = f'Using custom reset sequence from esptool config file: {os.path.join(os.getcwd(), "config.cfg")}'
        assert msg in stderr
        # remove everything before message about using custom config to remove starting reset sequence
        log_seq = stderr.split(msg)[1]
        # Check pyserial's log of the custom reset sequence. The esp-pylib
        # serial-reset primitives pass ``True``/``False`` to ``setRTS`` /
        # ``setDTR`` (matching the type annotations); the legacy esp-idf-monitor
        # implementation passed the integer literals from the ``R1`` / ``R0``
        # tokens directly. Both render the same SET_CONTROL_LINE_STATE on the
        # wire, but pyserial logs the Python value as-is. Match either form.
        my_seq = [
            'INFO:pySerial.socket:ignored _update_rts_state(True)',  # R1
            'INFO:pySerial.socket:ignored _update_dtr_state(False)',  # expected workaround for windows RTS setting
            'INFO:pySerial.socket:ignored _update_rts_state(False)',  # R0
            'INFO:pySerial.socket:ignored _update_dtr_state(False)',  # expected workaround for windows RTS setting
            'INFO:pySerial.socket:ignored _update_dtr_state(True)',  # D1
        ]
        assert '\n'.join(my_seq) in log_seq

    def test_custom_sequence_precedence(self):
        """Define custom reset sequence in esptool and esp-idf-monitor sections and
        make sure that the one from esp-idf-monitor is used"""
        # create custom config with custom reset sequence
        filename = self.create_config({'custom_reset_sequence': 'R1|W0.1|R0|D1'})
        with open(filename, 'a') as f:
            f.writelines(['[esptool]\n', 'custom_reset_sequence = R1|D1\n'])
        # run monitor
        _, err = self.run_monitor_async(args=['--no-reset'])
        # reset into bootloader
        self.send_control('TP')
        # wait for command to apply
        time.sleep(0.5)
        assert self.close_monitor_async() == 0

        with open(err) as f_err:
            stderr = f_err.read()
        msg = f'Using custom reset sequence from config file: {os.path.join(os.getcwd(), "config.cfg")}'
        assert msg in stderr
        # remove everything before message about using custom config to remove starting reset sequence
        log_seq = stderr.split(msg)[1]
        # See ``test_esptool_sequence`` for the rationale on ``True``/``False`` here
        my_seq = [
            'INFO:pySerial.socket:ignored _update_rts_state(True)',  # R1
            'INFO:pySerial.socket:ignored _update_dtr_state(False)',  # expected workaround for windows RTS setting
            'INFO:pySerial.socket:ignored _update_rts_state(False)',  # R0
            'INFO:pySerial.socket:ignored _update_dtr_state(False)',  # expected workaround for windows RTS setting
            'INFO:pySerial.socket:ignored _update_dtr_state(True)',  # D1
        ]
        assert '\n'.join(my_seq) in log_seq

    def test_invalid_custom_sequence(self):
        """Use invalid custom reset sequence"""
        # create custom config with custom reset sequence
        self.create_config({'custom_reset_sequence': 'FOO'})
        # run monitor
        _, err = self.run_monitor_async()
        # reset into bootloader
        self.send_control('TP')
        # wait for command to apply
        time.sleep(0.5)
        assert self.close_monitor_async() == 0

        with open(err) as f_err:
            stderr = f_err.read()
        # check for error message that reset sequence was invalid. esp-pylib's
        # ``parse_custom_reset_sequence`` reports the full bad token in its
        # error message (``Invalid custom reset sequence step 'FOO': Unknown
        # reset sequence command: 'FOO'.``), which is more useful than the
        # legacy ``'F'`` (the KeyError'd first character) — assert on a
        # substring that's stable across both phrasings.
        assert f'Using custom reset sequence from config file: {os.path.join(os.getcwd(), "config.cfg")}' in stderr
        assert 'Invalid "custom_reset_sequence" option format:' in stderr
        assert "'FOO'" in stderr

    def test_custom_hard_reset_sequence(self):
        """Use custom hard reset sequence"""
        # create custom config with custom hard reset sequence
        self.create_config({'custom_hard_reset_sequence': 'R1|W0.1|R0'})
        # run monitor
        _, err = self.run_monitor_async(args=['--no-reset'])
        # hard reset chip
        self.send_control('TR')
        # wait for command to apply
        time.sleep(0.5)
        assert self.close_monitor_async() == 0
        with open(err) as f_err:
            stderr = f_err.read()
        msg = f'Using custom hard reset sequence from config file: {os.path.join(os.getcwd(), "config.cfg")}'
        assert msg in stderr
        # remove everything before message about using custom config to remove starting reset sequence
        log_seq = stderr.split(msg)[1]
        # See ``test_esptool_sequence`` for the rationale on ``True``/``False``
        # here vs. the historical ``1``/``0``.
        my_seq = [
            'INFO:pySerial.socket:ignored _update_rts_state(True)',  # R1
            'INFO:pySerial.socket:ignored _update_dtr_state(False)',  # expected workaround for windows RTS setting
            'INFO:pySerial.socket:ignored _update_rts_state(False)',  # R0
        ]
        assert '\n'.join(my_seq) in log_seq


class TestCStyleConversion(TestBaseClass):
    """Test C-style conversion"""

    @pytest.mark.parametrize(
        'c_fmt, arg, pythonic_fmt, output',
        [
            # String formatting
            ('|%s|', 'Hello_world', '{:>s}', '|Hello_world|'),
            ('|%10s|', 'ESP32', '{:>10s}', '|     ESP32|'),
            ('|%-10s|', 'ESP32', '{:<10s}', '|ESP32     |'),
            ('|%.5s|', 'Hello_world', '{:>.5s}', '|Hello|'),
            # Character formatting
            ('|%c|', chr(65), '{:s}', '|A|'),
            # Integer formatting
            ('|%d|', 123, '{:d}', '|123|'),
            ('|%5d|', 42, '{:5d}', '|   42|'),
            ('|%05d|', 42, '{:05d}', '|00042|'),
            ('|%-5d|', 42, '{:<5d}', '|42   |'),
            ('|%.5d|', 42, '{:05d}', '|00042|'),
            ('|%+d|', 42, '{:+d}', '|+42|'),
            ('|% d|', 42, '{: d}', '| 42|'),
            ('|%ld|', 123456789, '{:d}', '|123456789|'),
            ('|%lld|', 1234567890123456789, '{:d}', '|1234567890123456789|'),
            ('|%#d|', 123456789, '{:d}', '|123456789|'),
            ('|%+10d|', 42, '{:+10d}', '|       +42|'),
            ('|% 10d|', 42, '{: 10d}', '|        42|'),
            ('|%-+10d|', 42, '{:<+10d}', '|+42       |'),
            ('|%- 10d|', 42, '{:< 10d}', '| 42       |'),
            # Pointer formatting
            ('|%p|', 0x3FF26523, '{:#x}', '|0x3ff26523|'),
            # Hexadecimal formatting
            ('|%x|', 255, '{:x}', '|ff|'),
            ('|%X|', 255, '{:X}', '|FF|'),
            ('|%05x|', 255, '{:05x}', '|000ff|'),
            ('|%.5x|', 255, '{:05x}', '|000ff|'),
            ('|%-5x|', 255, '{:<5x}', '|ff   |'),
            ('|%+x|', 42, '{:+x}', '|+2a|'),
            ('|% x|', 42, '{: x}', '| 2a|'),
            ('|%hx|', 0xFFFF, '{:x}', '|ffff|'),
            ('|%hhx|', 0xFF, '{:x}', '|ff|'),
            ('|%#x|', 42, '{:#x}', '|0x2a|'),
            ('|%#X|', 255, '{:#X}', '|0XFF|'),
            ('|%#10x|', 42, '{:#10x}', '|      0x2a|'),
            ('|%-#10x|', 42, '{:<#10x}', '|0x2a      |'),
            # Octal formatting
            ('|%o|', 8, '{:o}', '|10|'),
            ('|%#o|', 8, '{:#o}', '|010|'),
            ('|%ho|', 511, '{:o}', '|777|'),
            ('|%#ho|', 511, '{:#o}', '|0777|'),
            ('|%#10o|', 42, '{:#10o}', '|       052|'),
            ('|%-#10o|', 42, '{:<#10o}', '|052       |'),
            # Float formatting
            ('|%f|', 123.456, '{:f}', '|123.456000|'),
            ('|%.2f|', 123.456, '{:.2f}', '|123.46|'),
            ('|%.2f|', -123.456, '{:.2f}', '|-123.46|'),
            ('|%10.2f|', 3.14159, '{:10.2f}', '|      3.14|'),
            ('|%-10.2f|', 3.14159, '{:<10.2f}', '|3.14      |'),
            ('|%10.6f|', -123.45678933, '{:10.6f}', '|-123.456789|'),
            ('|%-10.6f|', -123.45678933, '{:<10.6f}', '|-123.456789|'),
            # Scientific float formatting
            ('|%F|', 123456.789, '{:F}', '|123456.789000|'),
            ('|%e|', 123456.789, '{:e}', '|1.234568e+05|'),
            ('|%E|', 123456.789, '{:E}', '|1.234568E+05|'),
            ('|%g|', 123456.789, '{:g}', '|123457|'),
            ('|%G|', 123456.789, '{:G}', '|123457|'),
            # Literal percent sign
            ('|%%|', '', '%', '|%|'),
            ('|%%| |%s|', 'Hello_world', '%', '|%| |Hello_world|'),
            ('|%d%%|', 12, '{:d}', '|12%|'),
            # } character in c-style format does not break pythonic format conversion
            ('} |%s|', 'Hello_world', '{:>s}', '} |Hello_world|'),
        ],
    )
    def test_c_format(self, c_fmt, arg, pythonic_fmt, output):
        """Test ArgFormatter.c_format with various format strings and arguments"""
        from esp_idf_monitor.base.binlog import ArgFormatter

        formatter = ArgFormatter()
        converted_format = formatter.convert_to_pythonic_format(formatter.c_format_regex.search(c_fmt))
        assert converted_format == pythonic_fmt, f"Expected Pythonic format '{pythonic_fmt}', got '{converted_format}'"
        formatted_output = formatter.c_format(c_fmt, [arg])
        assert formatted_output == output, f"Expected '{output}', got '{formatted_output}'"


class TestEmbeddedMonitorCommands:
    """Tests for SecureMonitorCommandExecutor handling embedded monitor commands."""

    class DummyLogger:
        def __init__(self) -> None:
            self.outputs: List[bytes] = []

        def print(self, data: bytes) -> None:
            self.outputs.append(data)

    @pytest.mark.parametrize(
        'input_line, expect_called, expected_argv',
        [
            # Unknown marker type after IDF_MONITOR_EXECUTE_: should be ignored
            (
                'I (20) test: IDF_MONITOR_EXECUTE_UNKNOWN EFSR:esp32c3:100:AAA\n',
                False,
                None,
            ),
            # Marker without any arguments: should be ignored
            (
                'I (20) test: IDF_MONITOR_EXECUTE_ESPEFUSE_SUMMARY\n',
                False,
                None,
            ),
            # Valid ESPEFUSE_SUMMARY with token
            (
                'I (311) example: IDF_MONITOR_EXECUTE_ESPEFUSE_SUMMARY EFSR:esp32c3:100:AAA\n',
                True,
                ['espefuse', '--token', 'EFSR:esp32c3:100:AAA', 'summary', '--active'],
            ),
            # Valid ESPEFUSE_DUMP with token
            (
                'I (331) example: IDF_MONITOR_EXECUTE_ESPEFUSE_DUMP EFSR:esp32c3:100:AAA\n',
                True,
                ['espefuse', '--token', 'EFSR:esp32c3:100:AAA', 'dump'],
            ),
        ],
    )
    def test_monitor_embedded_command_execution(
        self,
        input_line: str,
        expect_called: bool,
        expected_argv: Optional[List[str]],
        monkeypatch,
    ):
        """
        Verify that SecureMonitorCommandExecutor:
        """
        from esp_idf_monitor.base.monitor_secure_exec import SecureMonitorCommandExecutor

        calls = []

        def fake_check_output(argv, stderr=None, env=None, shell=None):
            calls.append(
                {
                    'argv': argv,
                    'stderr': stderr,
                    'env': env,
                    'shell': shell,
                }
            )
            return b'OK\n'

        # Patch subprocess.check_output used inside monitor_secure_exec
        monkeypatch.setattr(
            'esp_idf_monitor.base.monitor_secure_exec.subprocess.check_output',
            fake_check_output,
        )

        logger = self.DummyLogger()
        executor = SecureMonitorCommandExecutor(logger)

        # Run executor for this test case (single full line, with '\n')
        executor.execute_from_log_line(input_line.encode('ascii'))

        if not expect_called:
            assert calls == []
            assert logger.outputs == []
            return

        # Exactly one subprocess call expected
        assert len(calls) == 1
        call = calls[0]
        argv = call['argv']

        # Full argv must match the expected expansion of the template
        assert argv == expected_argv

        # Explicitly verify that shell=False was used
        assert call['shell'] is False

        # Logger should receive the output from the subprocess
        assert logger.outputs == [b'OK\n']

    def test_monitor_embedded_command_streaming_chunks(self, monkeypatch):
        """
        Verify that execute_from_log_line handles partial lines and only
        executes once a complete line (with '\n') is received.
        """
        from esp_idf_monitor.base.monitor_secure_exec import SecureMonitorCommandExecutor

        calls = []

        def fake_check_output(argv, stderr=None, env=None, shell=None):
            calls.append(
                {
                    'argv': argv,
                    'stderr': stderr,
                    'env': env,
                    'shell': shell,
                }
            )
            return b'OK\n'

        monkeypatch.setattr(
            'esp_idf_monitor.base.monitor_secure_exec.subprocess.check_output',
            fake_check_output,
        )

        logger = self.DummyLogger()
        executor = SecureMonitorCommandExecutor(logger)

        # First chunk: no newline yet, should not trigger execution
        chunk1 = b'I (311) example: IDF_MONITOR_EXECUTE_ESPEFUSE_SUMMARY EFSR:esp32c3:100:AAA'
        executor.execute_from_log_line(chunk1)
        assert calls == []
        assert logger.outputs == []

        # Second chunk: completes the line with '\n'
        chunk2 = b'BBB\n'
        executor.execute_from_log_line(chunk2)

        # Now we expect exactly one call, with the combined token "AAABBB"
        assert len(calls) == 1
        call = calls[0]
        argv = call['argv']
        assert argv == ['espefuse', '--token', 'EFSR:esp32c3:100:AAABBB', 'summary', '--active']
        assert call['shell'] is False
        assert logger.outputs == [b'OK\n']


class TestLogger:
    class _DummyConsole:
        """Minimal stand-in for serial.tools.miniterm.Console (stored on Logger, unused in these checks)."""

        pass

    def test_disable_address_decoding_no_attribute_error(self):
        """With --disable-address-decoding, Logger must still expose pc_address_decoder=None."""
        logger = Logger(
            elf_files=['/nonexistent/example.elf'],
            console=self._DummyConsole(),
            timestamps=False,
            timestamp_format='',
            enable_address_decoding=False,
            toolchain_prefix='riscv32-esp-elf-',
        )
        assert logger.pc_address_decoder is None

        # SerialHandler always calls this when an ELF path was passed; must not raise AttributeError
        logger.handle_possible_pc_address_in_line(b'abort() was called at PC 0x420061ad on core 0\n')
        logger.handle_possible_pc_address_in_line(b'PC      : 0x40080123\n')

        assert logger.pc_address_buffer == b''
        logger.pc_address_buffer = b'suffix'
        assert logger.pc_address_buffer == b''

    def test_timestamps_unaffected_by_monitor_messages(self):
        """Monitor stderr lines must not break timestamp prefixing on serial output."""
        serial_output = []  # type: List[bytes]

        class _CapturingConsole:
            def write_bytes(self, data):  # type: (bytes) -> None
                serial_output.append(data)

        logger = Logger(
            elf_files=['/nonexistent/example.elf'],
            console=_CapturingConsole(),
            timestamps=True,
            timestamp_format='%Y-%m-%d %H:%M:%S',
            enable_address_decoding=False,
            toolchain_prefix='riscv32-esp-elf-',
        )
        ts_pattern = re.compile(rb'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ')

        with patch.object(log, 'print'):
            logger.print(b'abort() was called at PC 0x42007bfd on core 0\n')
            logger.print('0x42007bfd: app_main at hello_world_main.c:52')
            logger.print('[yellow]Stack dump detected[/yellow]\n')
            logger.print(b'Core  0 register dump:\n')
            logger.print(b'MEPC    : 0x40805414  RA      : 0x408053d2  SP      : 0x4080d690  GP      : 0x408091a4\n')
            logger.print('0x40805414: panic_abort at panic.c:496')
            logger.print('0x408053d2: esp_vApplicationTickHook at freertos_hooks.c:31')
            logger.print(b'TP      : 0x4080d780  T0      : 0x37363534\n')

        assert len(serial_output) == 4
        for chunk in serial_output:
            assert ts_pattern.match(chunk), f'missing timestamp on serial output: {chunk!r}'

    def test_monitor_messages_written_to_log_file(self, tmp_path, monkeypatch):
        """Monitor stderr strings must land in the log file with Rich markup stripped."""
        monkeypatch.chdir(tmp_path)

        class _CapturingConsole:
            def write_bytes(self, data: bytes) -> None:
                pass

        logger = Logger(
            elf_files=['example.elf'],
            console=_CapturingConsole(),
            timestamps=False,
            timestamp_format='',
            enable_address_decoding=False,
            toolchain_prefix='riscv32-esp-elf-',
        )
        logger.start_logging()
        try:
            with patch.object(log, 'print'):
                logger.print('[yellow]Stack dump detected[/yellow]')
                logger.print(b'Core  0 register dump:\n')
        finally:
            logger.stop_logging()

        log_files = list(tmp_path.glob('log.example.*.txt'))
        assert len(log_files) == 1
        content = log_files[0].read_bytes()
        assert b'Stack dump detected' in content
        assert b'[yellow]' not in content
        assert b'Core  0 register dump:' in content


class TestFlashAllCommands:
    """Unit tests for full-flash (fast-reflash disable) keyboard / make wiring."""

    class _FakeLogger:
        output_enabled = False

    @staticmethod
    def _patch_popen(monkeypatch, *, returncode=0, captured=None):
        from esp_idf_monitor.base import serial_handler

        class FakePopen:
            def __init__(self, args, env=None):
                if captured is not None:
                    captured['args'] = list(args)
                    captured['env'] = env
                self.returncode = returncode

            def wait(self):
                return self.returncode

        monkeypatch.setattr(serial_handler.subprocess, 'Popen', FakePopen)

    def _run_make(self, **kwargs):
        from esp_idf_monitor.base import serial_handler

        serial_handler.run_make(
            kwargs.pop('target', 'flash'),
            kwargs.pop('make', ['python', 'idf.py']),
            console=None,
            console_parser=None,
            event_queue=None,
            cmd_queue=None,
            logger=self._FakeLogger(),
            **kwargs,
        )

    def test_console_parser_flash_all_key(self):
        """Menu + Ctrl-E / E maps to CMD_FLASH_ALL."""
        from esp_idf_monitor.base.key_config import MENU_KEY
        from esp_idf_monitor.base.key_config import RECOMPILE_UPLOAD_ALL_KEY

        parser = ConsoleParser()
        assert parser.parse(MENU_KEY) is None
        assert parser.parse(RECOMPILE_UPLOAD_ALL_KEY) == (TAG_CMD, CMD_FLASH_ALL)

        parser = ConsoleParser()
        assert parser.parse(MENU_KEY) is None
        assert parser.parse('E') == (TAG_CMD, CMD_FLASH_ALL)

    def test_help_mentions_full_flash(self):
        help_text = ConsoleParser().get_help_text()
        assert 'Build & flash project (fast reflash, ESP-IDF 6.1+)' in help_text
        assert 'Build & full flash project' in help_text

    def test_run_make_forwards_env_without_extra_args(self, monkeypatch):
        """run_make merges env_extra into the subprocess env and adds no CLI flags."""
        captured = {}
        self._patch_popen(monkeypatch, captured=captured)
        self._run_make(env_extra={'IDF_FLASH_FULL': '1'})
        assert captured['args'] == ['python', 'idf.py', 'flash']
        assert captured['env']['IDF_FLASH_FULL'] == '1'

    def test_flash_all_dispatch_sets_full_flash_env(self):
        """CMD_FLASH_ALL runs the flash target with IDF_FLASH_FULL=1; other paths set no env."""
        from esp_idf_monitor.base.serial_handler import SerialHandler

        calls = []

        def fake_run_make(target, **kwargs):
            calls.append((target, kwargs))

        # object.__new__ skips __init__; these command branches only read .encrypted.
        handler = object.__new__(SerialHandler)

        # Full flash: exports IDF_FLASH_FULL=1, no -a flag.
        handler.encrypted = False
        handler.handle_commands(CMD_FLASH_ALL, 'esp32', fake_run_make, None, None)
        assert calls == [('flash', {'env_extra': {'IDF_FLASH_FULL': '1'}})]

        # Encrypted full flash: esptool forces a full flash itself, so no env var.
        calls.clear()
        handler.encrypted = True
        handler.handle_commands(CMD_FLASH_ALL, 'esp32', fake_run_make, None, None)
        assert calls == [('encrypted-flash', {})]

        # Normal flash must not request a full flash.
        calls.clear()
        handler.encrypted = False
        handler.handle_commands(CMD_MAKE, 'esp32', fake_run_make, None, None)
        assert calls == [('flash', {})]


class TestTagKeyEncoding:
    """Regression tests for encoding console key events for the serial port."""

    def test_surrogateescape_key_roundtrips_to_raw_byte(self):
        """Invalid UTF-8 stdin bytes must not crash TAG_KEY handling (issue #44)."""
        written = []
        monitor = object.__new__(Monitor)
        monitor.cmd_queue = queue.Queue()
        monitor.event_queue = queue.Queue()
        monitor._flush_deadline = None
        monitor.serial_write = written.append  # type: ignore[method-assign]

        data = b'\xe3'.decode('utf-8', 'surrogateescape')
        monitor.event_queue.put((TAG_KEY, data))
        monitor._main_loop()

        assert written == [b'\xe3']
        # sanity: the previous strict encode path would have raised
        with pytest.raises(UnicodeEncodeError):
            codecs.encode(data)


class TestCommandReader:
    """Unit tests for the non-interactive command reader (CommandReader).

    The reader is exercised directly through _handle_line()/observe_line()
    without spawning the monitor, so these are fast and run on every platform.
    """

    def _reader(self, eol: str = 'CR') -> Tuple[queue.Queue, CommandReader]:
        event_queue: queue.Queue = queue.Queue()
        reader = CommandReader(event_queue, ConsoleParser(eol))
        return event_queue, reader

    def _drain(self, event_queue: queue.Queue) -> List[Tuple]:
        """Return everything queued so far, in order."""
        items = []
        while not event_queue.empty():
            items.append(event_queue.get_nowait())
        return items

    @pytest.mark.parametrize(
        'command, expected_cmd',
        [
            ('reset', CMD_RESET),
            ('flash', CMD_MAKE),
            ('flash-all', CMD_FLASH_ALL),
            ('app-flash', CMD_APP_FLASH),
            ('output', CMD_OUTPUT_TOGGLE),
            ('log', CMD_TOGGLE_LOGGING),
            ('timestamps', CMD_TOGGLE_TIMESTAMPS),
            ('bootloader', CMD_ENTER_BOOT),
        ],
    )
    def test_simple_command_maps_to_event(self, command: str, expected_cmd: int):
        """Each simple command is queued as a single (TAG_CMD, <cmd>) event."""
        event_queue, reader = self._reader()
        assert reader._handle_line(command) is True
        assert self._drain(event_queue) == [(TAG_CMD, expected_cmd)]

    def test_command_is_case_insensitive(self):
        """Commands are matched regardless of case."""
        event_queue, reader = self._reader()
        assert reader._handle_line('ReSeT') is True
        assert self._drain(event_queue) == [(TAG_CMD, CMD_RESET)]

    @pytest.mark.parametrize(
        'eol, expected',
        [
            ('CR', 'free\r'),
            ('LF', 'free\n'),
            ('CRLF', 'free\r\n'),
        ],
    )
    def test_send_translates_eol(self, eol: str, expected: str):
        """'send <text>' queues a key event with the text and target EOL."""
        event_queue, reader = self._reader(eol)
        assert reader._handle_line('send free') is True
        assert self._drain(event_queue) == [(TAG_KEY, expected)]

    def test_exit_stops_reading_and_requests_stop(self):
        """'exit' queues a stop command and returns False to stop reading."""
        event_queue, reader = self._reader()
        assert reader._handle_line('exit') is False
        assert self._drain(event_queue) == [(TAG_CMD, CMD_STOP)]

    @pytest.mark.parametrize('line', ['', '# a comment', '#reset'])
    def test_blank_and_comment_lines_are_ignored(self, line: str):
        """Empty lines and lines starting with '#' are skipped, queuing nothing."""
        event_queue, reader = self._reader()
        assert reader._handle_line(line) is True
        assert self._drain(event_queue) == []

    def test_unknown_command_is_reported_without_event(self):
        """An unknown command keeps the reader running but queues nothing."""
        event_queue, reader = self._reader()
        assert reader._handle_line('frobnicate') is True
        assert self._drain(event_queue) == []

    def test_invalid_sleep_duration_is_ignored(self):
        """A non-numeric sleep duration is reported but does not stop the script."""
        event_queue, reader = self._reader()
        assert reader._handle_line('sleep soon') is True
        assert self._drain(event_queue) == []

    def test_invalid_expect_pattern_stops_reader(self):
        """An invalid 'expect' regex aborts the script: stop is queued and reading stops."""
        event_queue, reader = self._reader()
        assert reader._handle_line('expect [unterminated') is False
        assert self._drain(event_queue) == [(TAG_CMD, CMD_STOP)]
        assert reader.exit_code == EXIT_SCRIPT_ERROR

    def test_expect_observe_line_matches_pattern(self):
        """observe_line wakes a pending 'expect' when a serial line matches."""
        _, reader = self._reader()
        reader._expect_pattern = re.compile('READY')
        reader.observe_line('I (123) app: device is READY now')
        assert reader._expect_matched.is_set()

    def test_expect_dollar_anchor_matches_despite_crlf(self):
        """Line endings are stripped before matching, so '$' works on CRLF output."""
        _, reader = self._reader()
        reader._expect_pattern = re.compile('READY$')
        reader.observe_line('I (123) app: READY\r\n')
        assert reader._expect_matched.is_set()

    def test_expect_non_matching_line_does_not_wake(self):
        """A non-matching line leaves the pending 'expect' unsatisfied."""
        _, reader = self._reader()
        reader._expect_pattern = re.compile('READY')
        reader.observe_line('I (123) app: still booting')
        assert not reader._expect_matched.is_set()

    def test_observe_line_without_armed_pattern_is_noop(self):
        """With no pending 'expect', observed lines do not wake it (and do not crash)."""
        _, reader = self._reader()
        reader.observe_line('anything at all')
        assert not reader._expect_matched.is_set()

    def test_expect_matches_already_buffered_line(self):
        """'expect' matches output received before it was armed (the look-back buffer)."""
        _, reader = self._reader()
        reader.observe_line('I (1) app: device READY now')  # buffered, no pattern armed yet
        reader._expect(re.compile('READY'))  # scans the look-back buffer
        assert reader._expect_matched.is_set()

    def test_expect_consumes_buffer_up_to_match(self):
        """A buffered match is consumed up to that line; later lines stay for the next 'expect'."""
        _, reader = self._reader()
        reader.observe_line('first line')
        reader.observe_line('the MATCH line')
        reader.observe_line('line after the match')
        reader._expect(re.compile('MATCH'))
        assert reader._expect_matched.is_set()
        # the line after the match is still available for the next 'expect'
        reader._expect(re.compile('after'))
        assert reader._expect_matched.is_set()

    def test_command_does_not_drop_buffered_output(self):
        """Commands must not clear the look-back buffer: a line observed around a
        command (e.g. the reply to a 'send') stays available to a following
        'expect'. Clearing it would be a cross-thread clear/observe race."""
        _, reader = self._reader()
        reader.observe_line('I (1) app: READY')  # buffered before the command
        reader._handle_line('reset')  # must not drop the buffer
        reader._expect(re.compile('READY'))  # still matched
        assert reader._expect_matched.is_set()

    # --- expect --timeout parsing tests ---

    @pytest.mark.parametrize(
        'line, expected_pattern, expected_timeout',
        [
            ('expect --timeout 10 Hello world!', 'Hello world!', 10.0),
            ('expect --timeout 0.5 READY$', 'READY$', 0.5),
            (
                r'expect --timeout 10 Minimum free heap size: \d+ bytes$',
                r'Minimum free heap size: \d+ bytes$',
                10.0,
            ),
            ('expect --timeout 10 42', '42', 10.0),
            ('expect 10', '10', None),
        ],
        ids=['basic', 'float_seconds', 'pattern_with_spaces', 'numeric_pattern', 'untimed_digits'],
    )
    def test_expect_timeout_parsing(self, line, expected_pattern, expected_timeout):
        """Valid 'expect' lines parse into the expected pattern and timeout."""
        _, reader = self._reader()

        def mock_expect(pattern, timeout=None):
            mock_expect.called_with = (pattern.pattern, timeout)
            return True  # pretend the pattern matched

        reader._expect = mock_expect  # type: ignore[assignment]
        assert reader._handle_line(line) is True
        assert mock_expect.called_with == (expected_pattern, expected_timeout)

    @pytest.mark.parametrize(
        'line',
        [
            'expect --timeout 10',  # missing regex
            'expect --timeout',  # missing seconds and regex
            'expect --timeout abc Hello',  # non-numeric seconds
            'expect --timeout 0 Hello',  # zero seconds
            'expect --timeout -1 Hello',  # negative seconds
            'expect --timeout inf Hello',  # infinite seconds
            'expect --timeout nan Hello',  # NaN seconds
        ],
    )
    def test_expect_timeout_invalid_usage_stops_reader(self, line: str):
        """Invalid --timeout usage aborts the script: stop is queued and reading stops."""
        event_queue, reader = self._reader()
        assert reader._handle_line(line) is False
        assert self._drain(event_queue) == [(TAG_CMD, CMD_STOP)]
        assert reader.exit_code == EXIT_SCRIPT_ERROR

    def test_expect_timeout_match_before_deadline(self):
        """'expect --timeout' returns immediately when the pattern matches from the buffer."""
        _, reader = self._reader()
        reader.observe_line('I (1) app: READY')
        assert reader._expect(re.compile('READY'), timeout=10.0) is True
        assert reader._expect_matched.is_set()

    def test_expect_timeout_expires_without_match(self):
        """'expect --timeout 0.2' gives up after the timeout when no line matches."""
        _, reader = self._reader()
        reader._thread = threading.current_thread()  # make alive=True
        start = time.monotonic()
        assert reader._expect(re.compile('NEVER_APPEARS'), timeout=0.2) is False
        elapsed = time.monotonic() - start
        assert not reader._expect_matched.is_set()
        assert elapsed >= 0.2
        assert elapsed < 2.0  # should not hang

    def test_expect_timeout_aborts_script(self):
        """A timed out 'expect' queues the stop and stops reading further commands."""
        event_queue, reader = self._reader()
        reader._thread = threading.current_thread()  # make alive=True
        assert reader._handle_line('expect --timeout 0.2 NEVER_APPEARS') is False
        assert self._drain(event_queue) == [(TAG_CMD, CMD_STOP)]
        assert reader.exit_code == EXIT_EXPECT_TIMEOUT

    def test_expect_stopped_while_waiting_is_not_a_timeout(self):
        """Being stopped (Ctrl+C, SIGTERM) while waiting does not count as a timeout."""
        _, reader = self._reader()
        reader._thread = threading.current_thread()  # make alive=True

        def delayed_stop():
            time.sleep(0.2)
            reader._thread = None  # what stop() does

        t = threading.Thread(target=delayed_stop)
        t.start()
        assert reader._expect(re.compile('NEVER_APPEARS'), timeout=60.0) is True
        t.join()
        assert not reader._expect_matched.is_set()

    def test_expect_timeout_match_from_observe_before_deadline(self):
        """A line arriving via observe_line before the timeout fires is still a match."""
        _, reader = self._reader()
        reader._thread = threading.current_thread()  # make alive=True

        def delayed_observe():
            time.sleep(0.1)
            reader.observe_line('device READY now')

        t = threading.Thread(target=delayed_observe)
        t.start()
        assert reader._expect(re.compile('READY'), timeout=5.0) is True
        t.join()
        assert reader._expect_matched.is_set()

    def test_expect_timeout_lookback_buffer_ignores_remaining_timeout(self):
        """A buffered match returns immediately even if a long timeout was set."""
        _, reader = self._reader()
        reader.observe_line('the MATCH line')
        start = time.monotonic()
        assert reader._expect(re.compile('MATCH'), timeout=60.0) is True
        elapsed = time.monotonic() - start
        assert reader._expect_matched.is_set()
        assert elapsed < 1.0  # immediate, not waiting 60s


@pytest.mark.skipif(os.name == 'nt', reason='Linux/MacOS only')
class TestCommandMode(TestBaseClass):
    """End-to-end tests for the non-interactive command mode.

    stdin is a pipe or /dev/null (never a TTY), so the monitor switches to
    command mode and reads line-based commands from stdin (CommandReader). The
    TCP server socket from the get_port fixture acts as the device the monitor
    is connected to.
    """

    def accept(self, timeout: int = 20) -> socket.socket:
        """Accept the monitor's serial-port connection on the server socket."""
        self.serversocket.settimeout(timeout)
        # annotate the local: self.serversocket is untyped (Any) in the base class
        clientsocket: socket.socket = self.serversocket.accept()[0]
        return clientsocket

    def wait_exit(self, timeout: int = 15) -> Optional[int]:
        """Wait for the monitor process to exit, failing the test on timeout."""
        watchdog = threading.Timer(timeout, on_timeout, [self.proc])
        watchdog.start()
        try:
            while True:
                ret = self.proc.poll()
                if ret is not None:
                    return ret
                time.sleep(0.2)
        finally:
            watchdog.cancel()

    def wait_for_output(self, path: str, needle: str, timeout: int = 10) -> bool:
        """Poll the output file until it contains needle or the timeout elapses."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with open(path) as f:
                if needle in f.read():
                    return True
            time.sleep(0.2)
        return False

    def teardown_method(self):
        """Make sure the monitor process is not left running."""
        proc = getattr(self, 'proc', None)
        try:
            if proc is not None and proc.poll() is None:
                proc.kill()
        except Exception:
            pass

    def test_command_mode_detected_and_eof_exits(self):
        """Non-TTY stdin selects command mode; the script runs and EOF exits."""
        out, err = self.run_monitor_command_mode()
        clientsocket = self.accept()  # wait for the monitor to connect its serial port
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(b'output\n')  # a harmless toggle, just to be echoed
            self.proc.stdin.close()  # EOF right after the one-line script
            ret = self.wait_exit()
        finally:
            clientsocket.close()
        assert ret == 0
        with open(err) as f_err:
            stderr = f_err.read()
        assert 'running in non-interactive mode' in stderr
        assert "--- Command: 'output'" in stderr
        assert 'EOF received on standard input, exiting' in stderr

    def test_sleep_then_expect_matches_serial_output(self):
        """'sleep' skips ahead, then 'expect' waits for a regex in the serial output.

        'expect' as the last command turns EOF into exit-on-pattern, and the
        '$' anchor matches despite the CRLF line ending from the device.
        """
        out, err = self.run_monitor_command_mode()
        clientsocket = self.accept()
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(b'sleep 0.2\nexpect READY$\n')
            self.proc.stdin.close()
            # give the reader time to consume 'sleep' and arm 'expect'
            time.sleep(1)
            clientsocket.sendall(b'I (100) app: still booting\r\n')
            clientsocket.sendall(b'I (200) app: READY\r\n')
            ret = self.wait_exit()
        finally:
            clientsocket.close()
        assert ret == 0
        with open(err) as f_err:
            stderr = f_err.read()
        assert "Expect pattern 'READY$' matched" in stderr

    def test_send_writes_to_the_device(self):
        """'send <text>' writes the text (followed by EOL) to the serial device."""
        out, err = self.run_monitor_command_mode()
        clientsocket = self.accept()
        clientsocket.settimeout(15)
        received = b''
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(b'send hello\nexit\n')
            self.proc.stdin.close()
            while b'hello' not in received:
                try:
                    chunk = clientsocket.recv(1024)
                except socket.timeout:
                    break
                if not chunk:
                    break
                received += chunk
            ret = self.wait_exit()
        finally:
            clientsocket.close()
        assert b'hello' in received
        assert ret == 0

    def test_watch_only_mode_exits_on_sigterm(self):
        """Empty stdin selects watch-only mode; SIGTERM shuts the monitor down cleanly."""
        out, err = self.run_monitor_command_mode(stdin=subprocess.DEVNULL)
        clientsocket = self.accept()
        printed = False
        try:
            clientsocket.sendall(b'I (1) app: hello from device\r\n')
            # wait until the line has actually been decoded and printed
            printed = self.wait_for_output(out, 'hello from device')
            self.proc.terminate()  # SIGTERM -> clean shutdown (like docker stop)
            ret = self.wait_exit()
        finally:
            clientsocket.close()
        assert printed, 'serial output was not printed in watch-only mode'
        assert ret == 0
        with open(err) as f_err:
            stderr = f_err.read()
        assert 'No commands on standard input, watching serial output only' in stderr

    def test_expect_timeout_aborts_script(self):
        """'expect --timeout' gives up after the deadline and aborts the rest of the script."""
        out, err = self.run_monitor_command_mode()
        clientsocket = self.accept()
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(b'expect --timeout 1 THIS_WILL_NOT_APPEAR\nsend NOT_REACHED\n')
            self.proc.stdin.close()
            # the pattern never appears on the serial port → timeout fires
            ret = self.wait_exit(timeout=15)
        finally:
            clientsocket.close()
        assert ret == EXIT_EXPECT_TIMEOUT
        with open(err) as f_err:
            stderr = f_err.read()
        assert 'timed out after 1' in stderr
        assert 'THIS_WILL_NOT_APPEAR' in stderr
        # the command after the timed out 'expect' was never read
        assert 'NOT_REACHED' not in stderr
