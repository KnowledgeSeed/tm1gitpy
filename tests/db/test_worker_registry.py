"""WorkerDBRegistry smoke tests."""

from pathlib import Path

from tm1_git_py.db._worker_db import WorkerDBRegistry


def test_registry_acquire_returns_working_sqlite_worker(tmp_path: Path) -> None:
    db_path = str(tmp_path / "registry-smoke.sqlite")
    lease = WorkerDBRegistry.acquire(
        db_path,
        execute_init=("PRAGMA journal_mode=WAL",),
    )
    try:
        assert lease.worker.execute_and_fetch("SELECT 1 AS x", []) == [(1,)]
    finally:
        lease.release()
        WorkerDBRegistry.force_close(db_path)
