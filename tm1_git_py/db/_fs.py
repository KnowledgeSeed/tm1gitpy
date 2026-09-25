"""Filesystem helpers for sqlite-backed caches."""

from __future__ import annotations

from pathlib import Path


def unlink_sqlite_artifacts(db_path: str) -> bool:
    """Remove the main db file and WAL/SHM sidecars if present. Idempotent.

    Returns True if any file was actually removed.
    """
    path = Path(db_path)
    removed_any = False
    for candidate in (
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    ):
        try:
            candidate.unlink()
            removed_any = True
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return removed_any
