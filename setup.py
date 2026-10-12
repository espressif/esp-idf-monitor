# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
import importlib.util

import setuptools

# Python 3.7 and 3.8 cannot install setuptools 77, the first version that
# accepts license_expression. setuptools 77 and newer can only use it when
# packaging 24.2 or newer is importable.
major = setuptools.__version__.split('.')[0]
if major.isdigit() and int(major) >= 77 and importlib.util.find_spec('packaging.licenses'):
    setuptools.setup(license_expression='Apache-2.0')
else:
    setuptools.setup(license='Apache-2.0')
