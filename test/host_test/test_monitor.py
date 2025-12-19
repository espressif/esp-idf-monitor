#!/usr/bin/env python
#
# SPDX-FileCopyrightText: 2018-2024 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
import datetime
import errno
import filecmp
import os
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

import pytest

from .conftest import out_dir

if os.name != 'nt':
    import pty

HOST = 'localhost'

IN_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'inputs')  # input files for tests
EXIT_KEY = b'\x1d\n'  # CTRL+]


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
        Note: Monitor needs to be running in async mode with 'ignore_input' set to False
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

    def run_monitor_async(
        self, args: List[str] = [], custom_port: str = '', ignore_input: bool = False
    ) -> Tuple[str, str]:
        """Run monitor in async mode
        ignore_input=True will disable input and enable monitor test mode
        Returns filenames for stdout and stderr
        """
        cmd = [
            sys.executable,
            '-m',
            'esp_idf_monitor',
            '--port',
            custom_port if custom_port else f'socket://{HOST}:{self.port}?logging=debug',
        ] + args
        env = os.environ.copy()
        if ignore_input:
            # enable closing the monitor from a socket and disable reading the input
            env['ESP_IDF_MONITOR_TEST'] = '1'
        output_file = os.path.join(out_dir, filename_fix(self.test_name))
        if os.name == 'nt':
            self.master_fd, self.slave_fd = None, None
        else:
            # stdin needs to be connected to some pseudo-tty in docker image even when it is not used at all
            self.master_fd, self.slave_fd = pty.openpty()
        with open(f'{output_file}.out', 'w') as o_f, open(f'{output_file}.err', 'w') as e_f:
            self.proc = subprocess.Popen(cmd, env=env, stdin=self.slave_fd, stdout=o_f, stderr=e_f)
        # make sure monitor is running before sending data
        time.sleep(3 if os.name == 'nt' else 1)
        return f'{output_file}.out', f'{output_file}.err'

    def run_monitor(
        self, args: List[str], input_file: str, custom_port: str = '', timeout: int = 60
    ) -> Tuple[str, str]:
        """Run IDF Monitor in test mode with timeout
        Returns filenames for stdout and stderr
        """
        out, err = self.run_monitor_async(args, custom_port=custom_port, ignore_input=True)
        # create a timer
        monitor_watchdog = threading.Timer(timeout, on_timeout, [self.proc])
        monitor_watchdog.start()

        # make sure that monitor is running, else we will end in an infinite loop
        if self.proc.poll() is not None:
            pytest.fail('Monitor has already ended')
        # send input file content to socket
        clientsocket, _ = self.serversocket.accept()
        try:
            with open(os.path.join(IN_DIR, input_file), 'rb') as f:
                for chunk in iter(lambda: f.read(1024), b''):
                    clientsocket.sendall(chunk)
            # end monitor
            clientsocket.sendall(EXIT_KEY)
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
        yield f'rfc2217://localhost:{rfc2217_port}?ign_set_control'
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
            assert 'Stopping condition has been received' in stderr
        assert self.filecmp(out, expected_out)

    def test_auto_color(self):
        """Test monitor auto-coloring feature"""
        # run monitor on empty input
        out, err = self.run_monitor([], 'color.txt')
        with open(err) as f_err:
            stderr = f_err.read()
            assert 'Stopping condition has been received' in stderr
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
        regex = re.compile(rf"--- esp-idf-monitor \d\.\d(\.\d)? on {re.escape(rfc2217)} \d*")
        assert regex.search(stderr) is not None
        assert 'Exception' not in stderr
        assert 'Stopping condition has been received' in stderr
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
        assert '--- Running make app-flash...' in stderr  # Triggered by TA
        assert '--- Running make flash...' in stderr  # TF
        assert '--- Error: Unknown menu character Ctrl+J' in stderr  # TJ

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
        assert "--- Warning: ELF file 'non_existing.elf' does not exist" in stderr

    def test_binary_logging(self):
        args = [os.path.join(IN_DIR, 'log.elf'), os.path.join(IN_DIR, 'bootloader.elf')]
        out, err = self.run_monitor(args, 'binlog', timeout=10)
        with open(err) as f_err:
            stderr = f_err.read()
            assert 'Stopping condition has been received' in stderr

        ansi_regex = re.compile(r'\x1B\[\d+(;\d+){0,2}m')
        with open(out) as f_out, open(os.path.join(IN_DIR, 'binlog_out.txt')) as f_expected:
            for line_out, line_expected in zip(f_out, f_expected):
                if os.name == 'nt':
                    line_expected = ansi_regex.sub('', line_expected)
                    line_out = ansi_regex.sub('', line_out)
                line_out = line_out.strip()
                line_expected = line_expected.strip()
                assert line_out == line_expected, f"Mismatch: {line_out} != {line_expected}"

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
            # Should be decoded as "I (259) example: >>> String Formatting Tests <<<"
            f.write(b'\x02\x0c\x10\x00\x00\x85\x9c\x3f\x40\x09\x9c\x00\x00\x01\x03\xd6')

        yield f.name
        os.unlink(f.name)

    def test_binary_log_invalid_data(self, invalid_binary_log: str):
        """Test the binary log with invalid data to make sure it is processed normally and not stuck"""
        args = [os.path.join(IN_DIR, 'log.elf')]
        out, err = self.run_monitor(args, invalid_binary_log, timeout=15)
        print('Using binary log file: ', invalid_binary_log)
        with open(err) as f_err:
            stderr = f_err.read()
            assert 'Stopping condition has been received' in stderr

        # Verify that monitor didn't get stuck and processed all data; ignore errors because we are using random data
        with open(out, errors='ignore') as f_out:
            output = f_out.read()
            # Should contain messages from both before and after invalid binary log processing
            assert 'I (1) main: Starting' in output
            assert 'I (1000) main: Application started' in output
            # Valid binary log data frame should be decoded
            assert 'I (259) example: >>> String Formatting Tests <<<' in output


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
        assert f'--- Loaded custom configuration from {os.path.join(os.getcwd(), filename)}' in stderr
        # check that help command contains values from the config
        assert '---    Ctrl+J         Reset target board via RTS line' in stderr
        assert 'Ctrl+R' not in stderr
        assert '---    Ctrl+K         Toggle saving output into file' in stderr
        assert 'Ctrl+L' not in stderr
        # make sure that logging was enabled
        regex = re.compile('--- Logging is enabled into file (.*\\.txt)')
        log_file = regex.search(stderr)
        assert log_file is not None
        # make sure that log file was closed on monitor exit
        assert f'--- Logging is disabled and file {log_file.groups()[0]} has been closed' in stderr

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
        assert '--- Running make app-flash...' in stderr  # Triggered by A

    def test_invalid_custom_config(self):
        # create custom config with unsupported value and unknown key
        self.create_config({'chip_reset_key': '.', 'foo': 'J'})
        # run monitor on empty input
        _, err = self.run_monitor_async()
        assert self.close_monitor_async() == 0

        with open(err) as f_err:
            stderr = f_err.read()
        # make sure that custom config was applied and stderr has message about it
        assert f'--- Loaded custom configuration from {os.path.join(os.getcwd(), "config.cfg")}' in stderr
        # check that stderr has message that config was not correct and fallback option works
        assert '--- Ignoring unknown configuration options: foo' in stderr
        assert (
            "--- Error: Unsupported configuration for key: '.', please use just the English alphabet "
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
        msg = f'--- Using custom reset sequence from esptool config file: {os.path.join(os.getcwd(), "config.cfg")}'
        assert msg in stderr
        # remove everything before message about using custom config to remove starting reset sequence
        log_seq = stderr.split(msg)[1]
        # check in pyserial log that custom reset sequence was used (Note: we cannot test the wait part)
        my_seq = [
            'INFO:pySerial.socket:ignored _update_rts_state(1)',  # R1
            'INFO:pySerial.socket:ignored _update_dtr_state(False)',  # expected workaround for windows RTS setting
            'INFO:pySerial.socket:ignored _update_rts_state(0)',  # R0
            'INFO:pySerial.socket:ignored _update_dtr_state(False)',  # expected workaround for windows RTS setting
            'INFO:pySerial.socket:ignored _update_dtr_state(1)',  # D1
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
        msg = f'--- Using custom reset sequence config file: {os.path.join(os.getcwd(), "config.cfg")}'
        assert msg in stderr
        # remove everything before message about using custom config to remove starting reset sequence
        log_seq = stderr.split(msg)[1]
        # check in pyserial log that custom reset sequence was used (Note: we cannot test the wait part)
        my_seq = [
            'INFO:pySerial.socket:ignored _update_rts_state(1)',  # R1
            'INFO:pySerial.socket:ignored _update_dtr_state(False)',  # expected workaround for windows RTS setting
            'INFO:pySerial.socket:ignored _update_rts_state(0)',  # R0
            'INFO:pySerial.socket:ignored _update_dtr_state(False)',  # expected workaround for windows RTS setting
            'INFO:pySerial.socket:ignored _update_dtr_state(1)',  # D1
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
        # check for error message that reset sequence was invalid
        assert f'--- Using custom reset sequence config file: {os.path.join(os.getcwd(), "config.cfg")}' in stderr
        assert '--- Error: Invalid "custom_reset_sequence" option format: \'F\'' in stderr


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
