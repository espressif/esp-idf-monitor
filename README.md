# Espressif IDF Monitor

The ```esp-idf-monitor``` is a Python-based, open-source package that is part of the [ESP-IDF](https://github.com/espressif/esp-idf) SDK for Espressif products.

The main responsibility of the IDF Monitor is serial communication input and output in ESP-IDF projects.

## Documentation

For information about basic usage and integration with ESP-IDF please see [IDF documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/tools/idf-monitor.html).
Other advanced topics like configuration file will be described in the following section.

### Table of Contents

- [Configuration File](#configuration-file)
  - [File Location](#file-location)
  - [Configuration Options](#configuration-options)
    - [Custom Reset Sequence](#custom-reset-sequence)
      - [Share Configuration Across Tools](#share-configuration-across-tools)
  - [Syntax](#syntax)

## Configuration File

`esp-idf-monitor` is using [C0 control codes](https://en.wikipedia.org/wiki/C0_and_C1_control_codes) to interact with the console. Characters from the config file are converted to their C0 control codes. Available characters include the English alphabet (A-Z) and special symbols: `[`, `]`, `\`, `^`, `_`.

> [!WARNING]
> Please note that some characters may not work on all platforms or can be already reserved as a shortcut for something else. Use this feature with caution!

### File Location

The default name for a configuration file is `esp-idf-monitor.cfg`. First, the same directory `esp-idf-monitor` is being run if is inspected.

If a configuration file is not found here, the current user's OS configuration directory is inspected next:

- **Linux:** `/home/<user>/.config/esp-idf-monitor/`
- **macOS:** `/Users/<user>/.config/esp-idf-monitor/`
- **Windows:** `c:\Users\<user>\AppData\Local\esp-idf-monitor\`

If a configuration file is still not found, the last inspected location is the home directory:

- **Linux:** `/home/<user>/`
- **macOS:** `/Users/<user>/`
- **Windows:** `c:\Users\<user>\`

On Windows, the home directory can be set with the `HOME` or `USERPROFILE` environment variables. Therefore, the Windows configuration directory location also depends on these.

A different location for the configuration file can be specified with the `ESP_IDF_MONITOR_CFGFILE` environment variable, e.g., `ESP_IDF_MONITOR_CFGFILE=~/custom_config.cfg`. This overrides the search priorities described above.

`esp-idf-monitor` will read settings from other usual configuration files if no other configuration file is used. It automatically reads from `setup.cfg` or `tox.ini` if they exist.

### Configuration Options

Below is a table listing the available configuration options:

| Option Name                | Description                                              | Default Value  |
|----------------------------|----------------------------------------------------------|----------------|
| `menu_key`                 | Key to access the main menu.                             | `T`            |
| `exit_key`                 | Key to exit the monitor.                                 | `]`            |
| `chip_reset_key`           | Key to initiate a chip reset.                            | `R`            |
| `recompile_upload_key`     | Key to recompile and upload.                             | `F`            |
| `recompile_upload_app_key` | Key to recompile and upload just the application.        | `A`            |
| `toggle_output_key`        | Key to toggle the output display.                        | `Y`            |
| `toggle_log_key`           | Key to toggle the logging feature.                       | `L`            |
| `toggle_timestamp_key`     | Key to toggle timestamp display.                         | `I`            |
| `chip_reset_bootloader_key`| Key to reset the chip to bootloader mode.                | `P`            |
| `exit_menu_key`            | Key to exit the monitor from the menu.                   | `X`            |
| `skip_menu_key`            | Pressing the menu key can be skipped for menu commands.  | `False`        |
| `reconnect_delay`          | Delay between reconnect retries (in seconds)             | 0.5            |
| `custom_reset_sequence`    | Custom reset sequence for resetting into the bootloader. | N/A            |

#### Custom Reset Sequence

For more advanced users or specific use cases, IDF Monitor supports the configuration of a custom reset sequence using [configuration file](#configuration-file). This is particularly useful in extreme edge cases where the default sequence may not suffice.

The sequence is defined with a string in the following format:

- Consists of individual commands divided by `|` (e.g. `R0|D1|W0.5`).
- Commands (e.g. `R0`) are defined by a code (`R`) and an argument (`0`).

| Code | Action                                                                  | Argument                |
|------|-------------------------------------------------------------------------|-------------------------|
| D    | Set DTR control line                                                    | `1`/`0`                 |
| R    | Set RTS control line                                                    | `1`/`0`                 |
| U    | Set DTR and RTS control lines at the same time (Unix-like systems only) | `0,0`/`0,1`/`1,0`/`1,1` |
| W    | Wait for `N` seconds (where `N` is a float)                             | N                       |

Example:

```ini
[esp-idf-monitor]
custom_reset_sequence = U0,1|W0.1|D1|R0|W0.5|D0
```

Refer to [custom reset sequence](https://docs.espressif.com/projects/esptool/en/latest/esptool/configuration-file.html#custom-reset-sequence) from Esptool documentation for further details. Please note that ``custom_reset_sequence`` is the only used value from the Esptool configuration, and others will be ignored in IDF Monitor.

##### Share Configuration Across Tools

The configuration for the custom reset sequence can be specified in a shared configuration file between IDF Monitor and Esptool. In this case, your configuration file name should be either `setup.cfg` or `tox.ini` so it would be recognized by both tools.

Example of a shared configuration file:

```ini
[esp-idf-monitor]
menu_key = T
skip_menu_key = True

[esptool]
custom_reset_sequence = U0,1|W0.1|D1|R0|W0.5|D0
```

> [!NOTE]
> When using the `custom_reset_sequence` parameter in both the `[esp-idf-monitor]` section and the `[esptool]` section, the configuration from the `[esp-idf-monitor]` section will take precedence in IDF Monitor. Any conflicting configuration in the `[esptool]` section will be ignored.
>
> This precedence rule also applies when the configuration is spread across multiple files. The global esp-idf-monitor configuration will take precedence over the local esptool configuration.

### Syntax

The configuration file is in .ini file format: it must be introduced by an `[esp-idf-monitor]` header to be recognized as valid. This section then contains `name = value` entries. Lines beginning with `#` or `;` are ignored as comments.

```ini
# esp-idf-monitor.cfg file to configure internal settings of esp-idf-monitor
[esp-idf-monitor]
menu_key = T
exit_key = ]
chip_reset_key = R
recompile_upload_key = F
recompile_upload_app_key = A
toggle_output_key = Y
toggle_log_key = L
toggle_timestamp_key = I
chip_reset_bootloader_key = P
exit_menu_key = X
skip_menu_key = False
```

## Contributing

### Code Style & Static Analysis

Please follow these coding standards when writing code for ``esp-idf-monitor``:

#### Pre-commit Checks

[pre-commit](https://pre-commit.com/) is a framework for managing pre-commit hooks. These hooks help to identify simple issues before committing code for review.

To use the tool, first install ``pre-commit``. Then enable the ``pre-commit`` and ``commit-msg`` git hooks:

```sh
python -m pip install pre-commit
pre-commit install -t pre-commit -t commit-msg
```

On the first commit ``pre-commit`` will install the hooks, subsequent checks will be significantly faster. If an error is found an appropriate error message will be displayed.

##### Codespell Check

This repository utilizes an automatic [spell checker](https://github.com/codespell-project/codespell) integrated into the pre-commit process. If any spelling issues are detected, the recommended corrections will be applied automatically to the file, ready for commit. In the event of false positives, you can adjust the configuration in the `pyproject.toml` file under the `[tool.codespell]` section. To exclude files from the spell check, utilize the `skip` keyword followed by comma-separated paths to the files (wildcards are supported). Additionally, to exclude specific words from the spell check, employ the `ignore-words-list` keyword followed by comma-separated words to be skipped.

#### Conventional Commits

``esp-idf-monitor`` complies with the [Conventional Commits standard](https://www.conventionalcommits.org/en/v1.0.0/#specification). Every commit message is checked with [Conventional Precommit Linter](https://github.com/espressif/conventional-precommit-linter), ensuring it adheres to the standard.

## License

This document and the attached source code are released as Free Software under Apache License Version 2. See the accompanying [LICENSE file](https://github.com/espressif/esp-idf-monitor/blob/master/LICENSE) for a copy.
