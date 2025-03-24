## v1.6.0 (2025-03-24)

### New Features

- **binlog**: Support binary log format expansion in monitor
- add support for new roms.json location

### Bug Fixes

- Catch all exceptions from esp-coredump package to avoid exiting monitor
- handle port disappear immediately after open
- Auto color for alternative timestamp formats
- Use absolute import in __main__ for pyinstaller/pyinstaller#2560
- include common prefix for each line in multiline string
- prevent address decode interleave with serial output

## v1.5.0 (2024-09-17)

### New Features

- add a common prefix for all messages originating from the monitor
- **esp-idf-monitor**: Add --open-port-attempts flag
- Add support for multiple ELF files
- **port_detection**: Filter out BT and WLAN debug serial ports on MacOS
- added auto color log feature

### Bug Fixes

- improve error message when STDIN in not attached to TTY

## v1.4.0 (2024-01-30)

### New Features

- Enable idf_monitor no_reset flag be set by environment variable
- add port advisory locking
- move decoding functions to esp-idf-panic-decoder

### Bug Fixes

- correctly decode string type in ANSIColorConverter
- print correct shortcut in toggle output message
- don't reset chip on reconnect
- unbuffered read for linux target
- **serial_reader**: don't set closing wait on already closed port
- running monitor on linux target does not need port
- **hard_reset**: make sure that DTR is pulled up before hard resetting the chip

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
