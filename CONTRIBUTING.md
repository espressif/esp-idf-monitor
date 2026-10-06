# Contributing to esp-idf-monitor

## Issues

Search the existing issues. If none covers your case, open an [issue](https://github.com/espressif/esp-idf-monitor/issues/new/choose) and discuss the change with the maintainers before you open a pull request (PR).

**Explain the problem before you propose a solution.** A clear account of why the problem happens and what you want to achieve is more useful than a solution with many uncertainties. In the issue, describe:

- What you want to achieve
- What happens instead, and why you think it happens
- How to reproduce it, with the full monitor output
- The chip, how it is connected (USB-to-UART bridge or USB-Serial/JTAG), and the versions of esp-idf-monitor, ESP-IDF, Python, and your operating system

## Checks

Use a virtual environment with a Python version that still receives [security updates](https://devguide.python.org/versions/). Some pre-commit hooks do not install on older versions. Set up the environment once per clone, from the repository root:

```sh
pip install -e ".[dev,host_test]"
pre-commit install
```

The hooks check the staged files and the commit message on every commit. Before you push, run:

```sh
pre-commit run --all-files
cd test
pytest host_test
```

- Some hooks fix files in place and then fail. Review and stage the changes, then commit again.
- The package must run on the oldest Python version that `requires-python` in `pyproject.toml` allows. The ruff and mypy hooks check the code against that version and reject newer syntax. The mypy hook also rejects newer standard library functions. It does not check the bodies of functions without type annotations. In those functions, check standard library calls yourself or add type annotations.
- On Linux and macOS, `test_binary_logging` fails unless `xtensa-esp32-elf-addr2line` is on `PATH`. Running ESP-IDF's export script in the same shell adds it.
- You do not need hardware. Maintainers run the tests in `test/test_apps` on ESP32 boards before merging.

## Commit Messages

Write the title as `<type>(<optional-scope>): <summary>`, for example `fix(logger): Write monitor messages to the log file`. The commit-msg hook applies the default rules of [conventional-precommit-linter](https://github.com/espressif/conventional-precommit-linter). Its README lists the allowed types and length limits. Running `pre-commit run --all-files` does not check commit messages. To check a message before you commit, save it to a file and run:

```sh
pre-commit run conventional-precommit-linter --hook-stage commit-msg --commit-msg-filename <file>
```

Do not edit `CHANGELOG.md`. It is generated from the commit titles at release time.

## Pull Requests

- Target the `master` branch. Keep one commit per logical change and squash fixup commits.
- In the description, link the issue (for example `Closes #123`), explain the problem the change solves, say how you tested it, and state what you could not verify.
- The `pre-commit.ci - pr` check runs the hooks again and may push an "Apply automatic fixes from pre-commit hooks" commit. Squash it into your commits.
- The `pull-request-style-linter` check warns about invalid commit messages, a description shorter than 50 characters, more than 5 commits, and a branch name with uppercase letters or more than one `/`.
- The `Build IDF Monitor binaries` checks build the standalone executables on six platforms. They fail if the change needs an unreleased version of a dependency.
