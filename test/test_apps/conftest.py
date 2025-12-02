import logging
import os
import sys
import time
from datetime import datetime
from typing import Any
from typing import Callable
from typing import List
from typing import Optional

import pytest
from pytest_embedded.plugin import multi_dut_argument
from pytest_embedded.plugin import multi_dut_fixture

DEFAULT_SDKCONFIG = 'default'


def get_param(item: pytest.Function, key: str, default: Any = None) -> Any:
    # implement like this since this is a limitation of pytest, couldn't get fixture values while collecting
    # https://github.com/pytest-dev/pytest/discussions/9689
    if not hasattr(item, 'callspec'):
        return default

    return item.callspec.params.get(key, default) or default


def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: List[pytest.Item]):
    """Modify test collection based on selected target"""
    target = config.getoption('--target')

    # Filter the test cases
    filtered_items = []
    for item in items:
        # filter by target
        all_markers = [marker.name for marker in item.iter_markers()]
        if target not in all_markers:
            continue

        filtered_items.append(item)

    # sort the test cases with (app folder, config)
    items[:] = sorted(
        filtered_items, key=lambda x: (os.path.dirname(x.path), get_param(x, 'config', DEFAULT_SDKCONFIG))
    )


@pytest.fixture(scope='session')
def coverage_run():
    """Run with coverage reporting if set by environment variable"""
    if 'COVERAGE_PROCESS_START' in os.environ:
        yield ['coverage', 'run', '--parallel-mode']
        # wait for coverage to write the data
        time.sleep(1)
    else:
        yield [sys.executable]


@pytest.fixture(scope='session', autouse=True)
def session_tempdir() -> str:
    _tmpdir = os.path.join(
        os.path.dirname(__file__),
        'pytest_embedded_log',
        datetime.now().strftime('%Y-%m-%d_%H-%M-%S'),
    )
    os.makedirs(_tmpdir, exist_ok=True)
    return _tmpdir


@pytest.fixture
@multi_dut_argument
def config(request: pytest.FixtureRequest) -> str:
    return getattr(request, 'param', None) or DEFAULT_SDKCONFIG


@pytest.fixture
@multi_dut_fixture
def build_dir(request: pytest.FixtureRequest, app_path: str, target: Optional[str], config: Optional[str]) -> str:
    """
    Check local build dir with the following priority:

    1. build_<$IDF_BRANCH>_<target>_<config>
    2. build_<$IDF_BRANCH>_<target>
    3. build_<target>_<config>
    4. build_<target>
    5. build_<config>
    6. build

    Returns:
        valid build directory
    """
    idf_version = os.getenv('IDF_BRANCH', None)
    check_dirs = []
    if idf_version is not None and target is not None and config is not None:
        check_dirs.append(f'build_{idf_version}_{target}_{config}')
    if idf_version is not None and target is not None:
        check_dirs.append(f'build_{idf_version}_{target}')
    if target is not None and config is not None:
        check_dirs.append(f'build_{target}_{config}')
    if target is not None:
        check_dirs.append(f'build_{target}')
    if config is not None:
        check_dirs.append(f'build_{config}')
    check_dirs.append('build')

    for check_dir in check_dirs:
        binary_path = os.path.join(app_path, check_dir)
        if os.path.isdir(binary_path):
            logging.info(f'find valid binary path: {binary_path}')
            return check_dir

        logging.warning('checking binary path: %s... missing... try another place', binary_path)

    recommend_place = check_dirs[0]
    logging.error(
        f'no build dir valid. Please build the binary via "idf.py -B {recommend_place} build" and run pytest again'
    )
    sys.exit(1)


@pytest.fixture(autouse=True)
@multi_dut_fixture
def junit_properties(test_case_name: str, record_xml_attribute: Callable[[str, object], None]) -> None:
    """
    This fixture is autoused and will modify the junit report test case name to <target>.<config>.<case_name>
    """
    record_xml_attribute('name', test_case_name)
