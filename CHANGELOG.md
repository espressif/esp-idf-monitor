<a href="https://www.espressif.com">
    <img src="https://www.espressif.com/sites/all/themes/espressif/logo-black.svg" align="right" height="20" />
</a>

# CHANGELOG

> All notable changes to this project are documented in this file.
> This list is not exhaustive - only important changes, fixes, and new features in the code are reflected here.

<div align="center">
    <a href="https://keepachangelog.com/en/1.1.0/">
        <img alt="Static Badge" src="https://img.shields.io/badge/Keep%20a%20Changelog-v1.1.0-salmon?logo=keepachangelog&logoColor=black&labelColor=white&link=https%3A%2F%2Fkeepachangelog.com%2Fen%2F1.1.0%2F">
    </a>
    <a href="https://www.conventionalcommits.org/en/v1.0.0/">
        <img alt="Static Badge" src="https://img.shields.io/badge/Conventional%20Commits-v1.0.0-pink?logo=conventionalcommits&logoColor=black&labelColor=white&link=https%3A%2F%2Fwww.conventionalcommits.org%2Fen%2Fv1.0.0%2F">
    </a>
    <a href="https://semver.org/spec/v2.0.0.html">
        <img alt="Static Badge" src="https://img.shields.io/badge/Semantic%20Versioning-v2.0.0-grey?logo=semanticrelease&logoColor=black&labelColor=white&link=https%3A%2F%2Fsemver.org%2Fspec%2Fv2.0.0.html">
    </a>
</div>
<hr>


## v1.8.0 (2025-09-12)

### ✨ New Features

- Build executables and attach to GitHub release *(Peter Dragun - fb2676e)*

### 🐛 Bug Fixes

- **binary_log**: Fix monitor being stuck on decoding invalid binary log data *(Peter Dragun - 3b0e86c)*


## v1.7.0 (2025-07-10)

### ✨ New Features

- **binlog**: Handle PC address in line for binlog *(Konstantin Kondrashov - 11ab615)*
- Improved addr2line output formatting *(Nebojsa Cvetkovic - d8932f3)*

### 🐛 Bug Fixes

- Add error message for Linux monitor when no ELF files are found *(Peter Dragun - c2bc767)*
- Update line matcher regex to handle all timestamp formats *(Peter Dragun - 74e6fb3)*


## v1.6.2 (2025-04-09)

### 🐛 Bug Fixes

- Install dependencies in the release workflow *(Peter Dragun - 181c49a)*

---

## v1.6.1 (2025-04-08)

### 🐛 Bug Fixes

- **binlog**: Fix binlog precision format *(Konstantin Kondrashov - 3a8cdf8)*

---

## v1.6.0 (2025-03-24)

### ✨ New Features

- **binlog**: Support binary log format expansion in monitor *(Konstantin Kondrashov - 815d48f)*
- add support for new roms.json location *(Peter Dragun - f8da3b4)*

### 🐛 Bug Fixes

- Catch all exceptions from esp-coredump package to avoid exiting monitor *(Peter Dragun - af197bd)*
- handle port disappear immediately after open *(Peter Dragun - f72aae3)*
- Auto color for alternative timestamp formats *(Nebojsa Cvetkovic - a96991f)*
- Use absolute import in __main__ for pyinstaller/pyinstaller#2560 *(Nebojsa Cvetkovic - 466235f)*
- include common prefix for each line in multiline string *(Peter Dragun - 2f18b23)*
- prevent address decode interleave with serial output *(Peter Dragun - 4e674ad)*

---

## v1.5.0 (2024-09-17)

### ✨ New Features

- **esp-idf-monitor**: Add --open-port-attempts flag *(Peter Dragun - 7f69908)*
- **port_detection**: Filter out BT and WLAN debug serial ports on MacOS *(Radim Karniš - 9713f51)*
- add a common prefix for all messages originating from the monitor *(Peter Dragun - 16cefd2)*
- Add support for multiple ELF files *(Peter Dragun - 40b1245)*
- added auto color log feature *(Peter Dragun - d49533f)*

### 🐛 Bug Fixes

- improve error message when STDIN in not attached to TTY *(Peter Dragun - 707c301)*

### 📖 Documentation

- Migrate configuration file documentation from ESP-IDF *(Peter Dragun - ab7722d)*

---

## v1.4.0 (2024-01-30)

### ✨ New Features

- Enable idf_monitor no_reset flag be set by environment variable *(Jan Beran - ee85119)*
- add port advisory locking *(Peter Dragun - be6f928)*
- move decoding functions to esp-idf-panic-decoder *(Peter Dragun - 4ecba50)*

### 🐛 Bug Fixes

- correctly decode string type in ANSIColorConverter *(Frantisek Hrbata - 9e6791b)*
- print correct shortcut in toggle output message *(Peter Dragun - 759c85d)*
- don't reset chip on reconnect *(Peter Dragun - efd1349)*

---

## v1.3.4 (2023-11-21)

### ✨ New Features

- **reset**: add custom and JTAG reset sequences *(Peter Dragun - c3dbf48)*
- **config**: allow reading config from other tools *(Peter Dragun - ba7901a)*

### 🐛 Bug Fixes

- **serial_reader**: don't set closing wait on already closed port *(Peter Dragun - 688e998)*
- **hard_reset**: make sure that DTR is pulled up before hard resetting the chip *(Peter Dragun - 7e4a3fc)*
- improve custom reset sequence config *(Peter Dragun - 49b68bb)*
- don't accept esptool.cfg as name for config *(Peter Dragun - 2fa9cfa)*
- unbuffered read for linux target *(Peter Dragun - ff1cca8)*
- running monitor on linux target does not need port *(Peter Dragun - d2bc23b)*

### 🔧 Code Refactoring

- convert host test to pytest *(Peter Dragun - 1a04d75)*

---

## v1.3.3 (2023-10-24)

### 🐛 Bug Fixes

- don't set closing wait on network ports *(Peter Dragun - 761f0e7)*

---

## v1.3.2 (2023-10-10)

### 🐛 Bug Fixes

- **print_filter**: fix setting print filter from env variable *(Peter Dragun - e9bb366)*
- IDE target test - use random port number *(Peter Dragun - 950d6d0)*

---

## v1.3.1 (2023-09-18)

### 🐛 Bug Fixes

- **idf_monitor**: don't discard input for all socket:// ports *(Ivan Grokhotkov - 0352943)*

---

## v1.3.0 (2023-09-08)

### ✨ New Features

- customizable config file for menu keys *(Peter Dragun - c294aab)*

### 🐛 Bug Fixes

- remove duplicated panic core dump when failed to execute panic decoder script *(Peter Dragun - 80570a3)*
- mypy fail when running pre-commit on Windows *(Jakub Kocka - 2ebe873)*

---

## v1.2.1 (2023-09-15)

### ✨ New Features

- **target_tests**: add ci configuration *(Peter Dragun - eb7f818)*
- **target_tests**: migrate first target tests from esp-idf *(Peter Dragun - c37cb8a)*
- **idf-monitor**: Add version to IDF Monitor output *(Jakub Kocka - 8e15627)*
- replace gdb_panic_server with new package *(Peter Dragun - dc672d6)*

### 🐛 Bug Fixes

- multibyte characters input causing windows to kill the console *(Peter Dragun - 43339b3)*
- dangerJS github action permissions *(Peter Dragun - cb3ca23)*

### 🔧 Code Refactoring

- move ASYNC_CLOSING_WAIT_NONE to constants *(Peter Dragun - dc66852)*

---

<div align="center">
    <small>
        <b>
            <a href="https://www.github.com/espressif/cz-plugin-espressif">Commitizen Espressif plugin</a>
        </b>
    <br>
        <sup><a href="https://www.espressif.com">Espressif Systems CO LTD. (2025)</a><sup>
    </small>
</div>
