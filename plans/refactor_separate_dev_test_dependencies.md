# Refactor summary: Separate dev/test dependencies from runtime

## Overview

Runtime and test/dev dependencies were split so that installing the package as a dependency (e.g. `pip install tm1gitpy` or `pip install -r requirements.txt`) no longer pulls in pytest, testcontainers, or other test-only packages. Test and dev dependencies are installed only when using `requirements-dev.txt` or the `[dev]` extra.

## Goal

- **Using the package as a dependency:** Only runtime dependencies are installed (TM1py, requests, PyYAML).
- **Running tests or developing:** Install runtime plus test/dev dependencies via `pip install -r requirements-dev.txt` or `pip install -e ".[dev]"`.

## Changes made

### 1. Requirement files

| File | Purpose |
|------|--------|
| **requirements.txt** | Runtime only: `TM1py>=2.1,<3.0`, `requests>=2.25`, `PyYAML>=6.0`. Used for production installs and as the base for dev. |
| **requirements-dev.txt** | New file. First line: `-r requirements.txt`. Then: `pytest>=7.0`, `pytest-mock`, `testcontainers>=4.0.0`, `nuitka`. Used for development and CI test jobs. |

### 2. pyproject.toml

- **PyYAML:** Added `PyYAML>=6.0` to `[project] dependencies` (used by the library at runtime; was previously only transitive or in requirements.txt).
- **Dev extra:** `[project.optional-dependencies] dev` was already present and left unchanged: pytest, pytest-mock, testcontainers. Installing with `pip install .[dev]` still installs these.

### 3. CI (.github/workflows/ci.yml)

- **test** job: Install step changed from `pip install -r requirements.txt` to `pip install -r requirements-dev.txt`.
- **integration-test** job: Same change; removed the redundant `pip install pytest testcontainers` line.
- **coverage** job: Install step changed to `pip install -r requirements-dev.txt` and `pip install pytest-cov`.
- **lint** and **build** jobs: Unchanged (lint uses runtime deps; build uses only build deps).

### 4. README.md

- **Installation:** Clarified “use” vs “run tests/develop”: use `pip install -e .` or `pip install -r requirements.txt` for runtime; use `pip install -r requirements-dev.txt` or `pip install -e ".[dev]"` for tests and development.
- **Requirements list:** Updated to include PyYAML >= 6.0 and to match the split (no test packages listed as core requirements).

## Usage after refactor

- **Consumers / CI that only need the library:** `pip install -r requirements.txt` or `pip install tm1gitpy`.
- **Contributors / CI that run tests:** `pip install -r requirements-dev.txt` or `pip install -e ".[dev]"`.

No application code changes were required; test imports are confined to test directories, so test-only packages are only needed when executing tests.
