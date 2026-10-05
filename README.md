# Espressif IDF Monitor

The `esp-idf-monitor` is a Python-based, open-source package that is part of the [ESP-IDF](https://github.com/espressif/esp-idf) SDK or can be used as a standalond tool for monitoring of Espressif microcontrollers.

The main responsibility of the IDF Monitor is serial communication input and output in ESP-IDF projects.

## Documentation

For information about basic usage and integration with ESP-IDF please see [IDF documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/tools/idf-monitor.html).
Other advanced topics like configuration file will be described in the following section.

### Table of Contents

- [Installation](#installation)
- [Usage](#usage)
- [Non-Interactive (Scripting) Mode](#non-interactive-scripting-mode)
- [Configuration File](#configuration-file)
  - [File Location](#file-location)
  - [Configuration Options](#configuration-options)
    - [Custom Reset Sequences](#custom-reset-sequences)
      - [Share Configuration Across Tools](#share-configuration-across-tools)
  - [Syntax](#syntax)
- [Embedded Command Execution](#embedded-command-execution)

## Installation

You can install the project with your favorite package manager, but for standalone installations it is recommended to use [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/stable/). Using one of the following commands:

> [!NOTE]
> It is always recommended to install packages in a virtual environment. Both tools recommended below will help you create a virtual environment.

```sh
uv tool install esp-idf-monitor
```

or

```sh
pipx install esp-idf-monitor
```

Both methods will install an `idf-monitor` executable for the current user without requiring administrator privileges.

## Usage

Usually IDF Monitor is run within ESP-IDF environment, which prefills a lot of arguments for features such as address decoding. However, IDF Monitor can work as standalone tool as well. For the standalone mode run:

```sh
idf-monitor
```

or

```sh
python -m esp_idf_monitor
```

For all parameters and their function please see `idf-monitor --help`.

## Non-Interactive (Scripting) Mode

The interactive key bindings require a terminal (TTY) on standard input. When standard input is **not** a TTY — for example a pipe, a file, or when the monitor runs in a CI job or a Docker container — the monitor automatically switches to a **non-interactive command mode** instead of exiting with an error. In this mode it reads line-based commands from standard input, so the monitor can be driven by a shell script, a CI job, or another program, while keeping all of its added value (log decoding, coloring, logging to a file).

There are no extra command-line options: the mode is selected automatically based on whether standard input is a TTY.

```sh
printf 'reset\nexpect ALL TESTS PASSED\n' | idf-monitor /dev/ttyUSB0
```

### Commands

| Command | Action |
| --- | --- |
| `reset` | Hard-reset the chip via the RTS line |
| `flash` | Run the make / `idf.py` `flash` target (fast reflash by default with ESP-IDF 6.1+) |
| `flash-all` | Run the `flash` target with full flash (disable fast reflash; equivalent to `idf.py flash -a`, ESP-IDF 6.1+) |
| `app-flash` | Run the make / `idf.py` `app-flash` target |
| `send <text>` | Send `<text>` followed by the end-of-line to the device |
| `sleep <seconds>` | Pause the script for the given time while serial output keeps flowing (accepts floats and `inf`) |
| `expect <regex>` | Block the script until a serial line matches the [regular expression](https://docs.python.org/3/library/re.html) (using `re.search`) |
| `expect --timeout <seconds> <regex>` | Like `expect`, but give up after `<seconds>` (a positive float) instead of waiting forever; giving up reports an error and aborts the script |
| `output` | Toggle printing of the serial output |
| `log` | Toggle saving the output into a file |
| `timestamps` | Toggle prepending timestamps to the output |
| `bootloader` | Reset the chip into the download (bootloader) mode |
| `exit` | Quit the monitor; pending serial output is drained first |

Empty lines and lines starting with `#` are ignored. Every processed command is echoed to **standard error**, so the progress of the script stays visible even when standard output is redirected to a file.

The line ending is stripped before an `expect` match, so a `$` anchor works regardless of whether the device terminates lines with `LF` or `CRLF`.

### Examples

**Wait for a pattern, then exit (exit-on-pattern).** As the last command of a script, `expect` turns the end of the script into an exit-on-pattern condition:

```sh
printf 'expect ALL TESTS PASSED\n' | idf-monitor /dev/ttyUSB0 > test.log
```

**Wait for a pattern with a timeout.** Without `--timeout` a script that never sees its pattern hangs forever. With it, the monitor reports an error on standard error after 10 seconds and stops, draining and flushing the log on the way out:

```sh
printf 'reset\nexpect --timeout 10 Hello world!\n' | idf-monitor /dev/ttyUSB0 > boot.log
```

The remaining commands of the script are **not** executed after a timeout: the expected output never arrived, so anything following it would run at a wrong moment. The process exits with code `110` so CI can distinguish a timeout from a clean run.

**Reset and capture a few seconds of the boot log.** No `exit` is needed — reaching the end of the script (EOF on standard input) ends the session:

```sh
printf 'reset\nsleep 10\n' | idf-monitor /dev/ttyUSB0 > boot.log
```

**Drive a console application.** `expect` works mid-script too, including on prompts printed without a line ending:

```sh
idf-monitor /dev/ttyUSB0 <<'EOF'
reset
expect esp>
send free
expect \d+
exit
EOF
```

**Watch-only mode in CI / Docker.** When standard input is empty from the start (for example `/dev/null`, or `docker run` without `-i`), there is no script to follow, so the monitor just watches the serial output until it is stopped from the outside (`Ctrl+C`, or `SIGTERM` from `docker stop` or a CI job timeout). A log file, if enabled, is flushed and closed on exit:

```sh
idf-monitor --save-log /dev/ttyUSB0 < /dev/null
```

> [!NOTE]
> Because there is no terminal, an interactive GDB session cannot be started from the chip-side GDB stub while running in non-interactive mode.

## Configuration File

`esp-idf-monitor` is using [C0 control codes](https://en.wikipedia.org/wiki/C0_and_C1_control_codes) to interact with the console. Characters from the config file are converted to their C0 control codes. Available characters include the English alphabet (A-Z) and special symbols: `[`, `]`, `\`, `^`, `_`.

> [!WARNING]
> Please note that some characters may not work on all platforms or can be already reserved as a shortcut for something else. Use this feature with caution!

### File Location

The default name for a configuration file is `esp-idf-monitor.cfg`. First, the same directory where `esp-idf-monitor` is being run is inspected.

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

| Option Name                  | Description                                                | Default Value  |
|------------------------------|------------------------------------------------------------|----------------|
| `menu_key`                   | Key to access the main menu.                               | `T`            |
| `exit_key`                   | Key to exit the monitor.                                   | `]`            |
| `chip_reset_key`             | Key to initiate a chip reset.                              | `R`            |
| `recompile_upload_key`       | Key to recompile and flash (fast reflash on ESP-IDF 6.1+). | `F`            |
| `recompile_upload_app_key`   | Key to recompile and flash just the application.           | `A`            |
| `recompile_upload_all_key`   | Key to recompile and full flash (ESP-IDF 6.1+).            | `E`            |
| `toggle_output_key`          | Key to toggle the output display.                          | `Y`            |
| `toggle_log_key`             | Key to toggle the logging feature.                         | `L`            |
| `toggle_timestamp_key`       | Key to toggle timestamp display.                           | `I`            |
| `chip_reset_bootloader_key`  | Key to reset the chip to bootloader mode.                  | `P`            |
| `exit_menu_key`              | Key to exit the monitor from the menu.                     | `X`            |
| `skip_menu_key`              | Pressing the menu key can be skipped for menu commands.    | `False`        |
| `reconnect_delay`            | Delay between reconnect retries (in seconds).              | 0.5            |
| `custom_reset_sequence`      | Custom reset sequence for resetting into the bootloader.   | N/A            |
| `custom_hard_reset_sequence` | Custom reset sequence for hard resetting the chip.         | N/A            |

#### Custom Reset Sequences

For more advanced users or specific use cases, IDF Monitor supports the configuration of custom reset sequences using [configuration file](#configuration-file). This is particularly useful in extreme edge cases where the default sequence may not suffice.

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

Refer to [custom reset sequence](https://docs.espressif.com/projects/esptool/en/latest/esptool/configuration-file.html#custom-reset-sequence) from Esptool documentation for further details. Please note that `custom_reset_sequence` and `custom_hard_reset_sequence` are the only used values from the Esptool configuration, and others will be ignored in IDF Monitor.

##### Share Configuration Across Tools

The configuration for the custom reset sequence can be specified in a shared configuration file between IDF Monitor and Esptool. In this case, your configuration file name should be either `setup.cfg` or `tox.ini` so it would be recognized by both tools.

Example of a shared configuration file:

```ini
[esp-idf-monitor]
menu_key = T
skip_menu_key = True

[esptool]
custom_reset_sequence = U0,1|W0.1|D1|R0|W0.5|D0
custom_hard_reset_sequence = R1|W0.1|R0
```

> [!NOTE]
> When using the `custom_reset_sequence` or `custom_hard_reset_sequence` parameter in both the `[esp-idf-monitor]` section and the `[esptool]` section, the configuration from the `[esp-idf-monitor]` section will take precedence in IDF Monitor. Any conflicting configuration in the `[esptool]` section will be ignored.
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
recompile_upload_all_key = E
toggle_output_key = Y
toggle_log_key = L
toggle_timestamp_key = I
chip_reset_bootloader_key = P
exit_menu_key = X
skip_menu_key = False
```

## Embedded Command Execution

`esp-idf-monitor` includes an advanced feature that automatically executes host-side tools when the target device outputs specific markers in its logs. This is particularly useful for workflows where device firmware needs to perform operations that are better handled on the host computer—such as decoding or analyzing chip data (for example, reading and interpreting eFuse dump).

### How It Works

When the monitor detects one of the predefined markers in the device output, it automatically executes the corresponding command template. The command substitutes data from the device output (such as eFuse tokens) into the template, allowing seamless data analysis without manual intervention.

### Supported Markers

The following markers are currently supported:

| Marker                                 | Command Template                           | Use Case                           |
|----------------------------------------|--------------------------------------------|------------------------------------|
| `IDF_MONITOR_EXECUTE_ESPEFUSE_SUMMARY` | `espefuse --token {ARGS} summary --active` | Display active eFuse summary       |
| `IDF_MONITOR_EXECUTE_ESPEFUSE_DUMP`    | `espefuse --token {ARGS} dump`             | Display eFuse dump                 |

For both commands, `{ARGS}` must include:
- A token eFuse dump (format: `EFSR:chiptype:size:hexdata...`)
- Optionally, additional flags such as `--extend-efuse-table main/esp_efuse_custom_table.csv` to extend eFuse field definitions

### Usage Example

When your firmware outputs a line containing `IDF_MONITOR_EXECUTE_ESPEFUSE_DUMP`:

```text
I (481) example: IDF_MONITOR_EXECUTE_ESPEFUSE_DUMP EFSR:esp32c3:100:AAAAAAAAAAAAAAAAAAAAAAAAAIAAAAAA:zIH3-VVgAAAAAAAAAAAAS8kmEVKwQgYB:ZSd8yloMSAJssOWmfZQw8lFbphuTZH574QcV3ggAAAA:AAAAAAAAAAEayAcAAAAAAAAAAAAAAAAAAAAAAAAAAAA:::::::::ydrNkQ
--- Executing monitor command: espefuse --token EFSR:esp32c3:100:... dump
espefuse v5.1.0
=== Run "dump" command ===
BLOCK0          (                ) [0 ] dump: 00000000 00000000 00000000 00000000 80000000 00000000
MAC_SPI_8M_0    (BLOCK1          ) [1 ] dump: f9f781cc 00006055 00000000 4b000000 521126c9 010642b0
BLOCK_SYS_DATA  (BLOCK2          ) [2 ] dump: ca7c2765 02480c5a a6e5b06c f230947d 1ba65b51 7b7e6493 de1507e1 00000008
...
I (331) example: read efuse fields
```

### Security and Limitations

For your security and to ensure predictable behavior, IDF Monitor:

- Does not execute arbitrary commands printed by the device
- Supports only a small, predefined set of markers mapped to fixed command templates
- Accepts only `<ARGS>` from the device—the eFuse token and optional flags—which are substituted into the template
- Executes all commands with `shell=False`, preventing shell metacharacters (`&&`, `;`, `|`, `>`) from being interpreted

This intentional limitation ensures that only specific, safe espefuse operations are available. Any future extensions would require careful review for security implications.

## Contributing

Open an [issue](https://github.com/espressif/esp-idf-monitor/issues) to discuss a change before you open a pull request. The [contribution guide](https://github.com/espressif/esp-idf-monitor/blob/master/CONTRIBUTING.md) lists the checks that must pass and explains how to write and check commit messages.

## License

This document and the attached source code are released as Free Software under Apache License Version 2. See the accompanying [LICENSE file](https://github.com/espressif/esp-idf-monitor/blob/master/LICENSE) for a copy.
