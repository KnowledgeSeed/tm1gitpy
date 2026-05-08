"""Autouse pytest fixture: close ModelStore / ChangesetStore workers and remove db files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tm1_git_py.db._worker_db import WorkerDBRegistry
from tm1_git_py.db.changeset_store import ChangesetStore
from tm1_git_py.db.model_store import ModelStore


def _unlink_sqlite_artifacts(db_path: str) -> None:
    """Remove the main db file and WAL journal sidecars if present."""
    path = Path(db_path)
    for candidate in (
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    ):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _snapshot_registered_store_paths() -> list[str]:
    paths: list[str] = []
    with ModelStore._instances_lock:
        paths.extend(os.path.abspath(s.db_path) for s in ModelStore._instances.values())
    with ChangesetStore._instances_lock:
        paths.extend(os.path.abspath(str(s.db_path)) for s in ChangesetStore._instances.values())
    seen: set[str] = set()
    deduped: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


@pytest.fixture(autouse=True)
def _close_sqlite_workers_per_test():
    """Tear down per-test sqlite state and delete associated db files on disk.

    Each test may open ``ModelStore`` / ``ChangesetStore`` instances that register
    background workers in ``WorkerDBRegistry``. Without teardown, workers (and
    open file handles) accumulate. Removing sqlite files avoids leftover WALs and
    caches under ``.tm1gitpy/.cache`` from growing across the suite.
    """
    yield
    paths = _snapshot_registered_store_paths()
    try:
        ModelStore.close_all()
    finally:
        try:
            ChangesetStore.close_all()
        finally:
            WorkerDBRegistry.close_all()
    for db_path in paths:
        _unlink_sqlite_artifacts(db_path)
