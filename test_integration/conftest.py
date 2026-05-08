"""Pytest discovery shim for the integration test package.

The shared fixtures live in :mod:`test_integration.test_base`. Re-exporting them
here lets pytest auto-apply ``autouse`` fixtures (notably the per-test sqlite
worker teardown) without each test module importing them explicitly.
"""

from test_integration.test_base import (  # noqa: F401
    _close_sqlite_workers_per_test,
    tm1_service,
)
