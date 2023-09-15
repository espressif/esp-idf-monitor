import os.path

from setuptools import find_packages, setup


def read(rel_path):
    here = os.path.abspath(os.path.dirname(__file__))
    with open(os.path.join(here, rel_path), 'r') as fp:
        return fp.read()


def get_version(rel_path):
    for line in read(rel_path).splitlines():
        if line.startswith('__version__'):
            delim = '"' if '"' in line else "'"
            return line.split(delim)[1]
    else:
        raise RuntimeError('Unable to find version string.')


def get_long_description():
    with open('README.md', 'r') as f:
        text = f.read()
    return text


setup(
    name='esp-idf-monitor',
    version=get_version('esp_idf_monitor/__init__.py'),
    author='Espressif Systems',
    author_email='',
    description='Serial monitor for esp-idf',
    long_description_content_type='text/markdown',
    long_description=get_long_description(),
    url='https://github.com/espressif/esp-idf-monitor',
    packages=find_packages(),
    python_requires='>=3.7',
    install_requires=[
        'pyserial>=3.3',
        'esp-coredump~=1.2',
        'pyelftools',
        'esp-idf-panic-decoder',
    ],
    extras_require={
        'dev': [
            'pre-commit',
        ],
        'ide': [
            'websocket-client'
        ],
        'test': [
            'SimpleWebSocketServer',
            'pytest_embedded',
            'pytest_embedded_idf',
            'pytest_embedded_serial_esp',
            'idf_build_apps~=1.0.1',
            'idf-component-manager',
        ]
    },
    keywords=['espressif', 'embedded', 'monitor', 'serial'],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'Environment :: Console',
        'Topic :: Software Development :: Embedded Systems',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Operating System :: POSIX',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: MacOS :: MacOS X',
    ],
)
