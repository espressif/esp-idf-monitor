## Unreleased

### New Features

- move decoding functions to esp-idf-panic-decoder

## v1.3.4 (2023-11-21)

### New Features

- **reset**: add custom and JTAG reset sequences
- **config**: allow reading config from other tools

### Bug Fixes

- improve custom reset sequence config
- don't accept esptool.cfg as name for config
- unbuffered read for linux target
- **serial_reader**: don't set closing wait on already closed port
- running monitor on linux target does not need port
- **hard_reset**: make sure that DTR is pulled up before hard resetting the chip

### Code Refactoring

- convert host test to pytest

## v1.3.3 (2023-10-24)

### Bug Fixes

- don't set closing wait on network ports

## v1.3.2 (2023-10-10)

### Bug Fixes

- **print_filter**: fix setting print filter from env variable
- IDE target test - use random port number

## v1.3.1 (2023-09-18)

### Bug Fixes

- **idf_monitor**: don't discard input for all socket:// ports
- remove duplicated panic core dump when failed to execute panic decoder script
