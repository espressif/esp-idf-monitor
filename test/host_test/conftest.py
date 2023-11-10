# SPDX-FileCopyrightText: 2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
out_dir = ''


def pytest_addoption(parser):
    parser.addoption('--output', action='store', default='./outputs/', help='Output directory for writing STDOUT and STDERR from tests')


def pytest_configure(config):
    global out_dir
    out_dir = config.getoption('--output')
