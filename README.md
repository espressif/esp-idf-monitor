# Espressif IDF Monitor

The ```esp-idf-monitor``` is a Python-based, open-source package that is part of the [ESP-IDF](https://github.com/espressif/esp-idf) SDK for Espressif products.

The main responsibility of the IDF Monitor is serial communication input and output in ESP-IDF projects. More information about IDF Monitor can be found in [IDF documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/tools/idf-monitor.html).

## Contributing

### Code Style & Static Analysis

Please follow these coding standards when writing code for ``esp-idf-monitor``:

#### Pre-commit checks

[pre-commit](https://pre-commit.com/) is a framework for managing pre-commit hooks. These hooks help to identify simple issues before committing code for review.

To use the tool, first install ``pre-commit``. Then enable the ``pre-commit`` and ``commit-msg`` git hooks:

```sh
python -m pip install pre-commit
pre-commit install -t pre-commit -t commit-msg
```

On the first commit ``pre-commit`` will install the hooks, subsequent checks will be significantly faster. If an error is found an appropriate error message will be displayed.


#### Conventional Commits

``esp-idf-monitor`` complies with the [Conventional Commits standard](https://www.conventionalcommits.org/en/v1.0.0/#specification). Every commit message is checked with [Conventional Precommit Linter](https://github.com/espressif/conventional-precommit-linter), ensuring it adheres to the standard.


## License

This document and the attached source code are released as Free Software under Apache License Version 2 or later. See the accompanying [LICENSE file](https://github.com/espressif/esp-idf-monitor/blob/master/LICENSE) for a copy.
