"""Process-wide :class:`SqliteWorker` registry and leases.

Application code uses :class:`SqliteWorker` synchronously (``fetch_all``,
``run_write``, ``commit``, etc.—see :mod:`tm1_git_py.db.sqlite_worker`).
This module owns shared worker instances keyed by ``(absolute_path, profile)``
so multiple store facades can share one background thread per database file.
"""

from __future__ import annotations

import inspect
import os
import threading
import time
from typing import Any, Optional, Sequence

from tm1_git_py.db.sqlite_worker import SqliteWorker


_DEFAULT_MAX_QUEUE_SIZE = 65_536
_DEFAULT_RESULT_TIMEOUT_SECONDS = 30.0


def sqlite_worker_constructor_kwargs(*, max_queue_size: int, max_count: int) -> dict[str, Any]:
    """Build kwargs for :class:`SqliteWorker` supported by the installed signature."""
    params = inspect.signature(SqliteWorker.__init__).parameters
    kwargs: dict[str, Any] = {}
    if "max_queue_size" in params:
        kwargs["max_queue_size"] = max_queue_size
    if "max_count" in params:
        kwargs["max_count"] = max_count
    return kwargs


def _apply_pragmas_and_drain(worker: SqliteWorker, init_sql: Sequence[str]) -> None:
    for sql in init_sql:
        worker.run_sync(sql)
    

class _WorkerDBRegistryEntry:
    def __init__(self, worker: SqliteWorker, *, execute_init: Sequence[str], max_queue_size: int, max_count: int) -> None:
        self.worker = worker
        self.execute_init = tuple(execute_init)
        self.max_queue_size = int(max_queue_size)
        self.max_count = int(max_count)
        self.refcount = 0
        self.last_used = time.monotonic()
        self.closed = False


class WorkerDBLease:
    """Lightweight reference to a registry-owned :class:`SqliteWorker`."""

    def __init__(self, registry: type["WorkerDBRegistry"], key: tuple[str, str], worker: SqliteWorker) -> None:
        self._registry = registry
        self._key = key
        self._worker = worker
        self._released = False

    @property
    def worker(self) -> SqliteWorker:
        return self._worker

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._registry.release(self._key)

    def close(self) -> None:
        self.release()


class WorkerDBRegistry:
    """Process-wide registry of sqlite workers keyed by absolute database path."""

    _lock = threading.RLock()
    _entries: dict[tuple[str, str], _WorkerDBRegistryEntry] = {}

    @classmethod
    def acquire(
        cls,
        file_name: str,
        *,
        execute_init: Sequence[str] = (),
        max_queue_size: int = _DEFAULT_MAX_QUEUE_SIZE,
        max_count: int = 50,
        profile: str = "rw",
        result_timeout_seconds: float = _DEFAULT_RESULT_TIMEOUT_SECONDS,
    ) -> WorkerDBLease:
        abs_path = os.path.abspath(file_name)
        key = (abs_path, str(profile or "rw"))
        init_sql = tuple(execute_init)
        worker_kw = sqlite_worker_constructor_kwargs(
            max_queue_size=max_queue_size,
            max_count=max_count,
        )
        with cls._lock:
            entry = cls._entries.get(key)
            if entry is not None and not entry.closed:
                entry.refcount += 1
                entry.last_used = time.monotonic()
                return WorkerDBLease(cls, key, entry.worker)
            if entry is not None and entry.closed:
                cls._entries.pop(key, None)

        candidate = SqliteWorker(
            abs_path,
            **worker_kw,
            result_timeout_seconds=float(result_timeout_seconds),
        )
        _apply_pragmas_and_drain(candidate, init_sql)
        candidate_entry = _WorkerDBRegistryEntry(
            candidate,
            execute_init=init_sql,
            max_queue_size=max_queue_size,
            max_count=max_count,
        )

        with cls._lock:
            entry = cls._entries.get(key)
            if entry is not None and not entry.closed:
                candidate_entry.closed = True
                candidate.close()
                entry.refcount += 1
                entry.last_used = time.monotonic()
                return WorkerDBLease(cls, key, entry.worker)
            if entry is not None and entry.closed:
                cls._entries.pop(key, None)
            cls._entries[key] = candidate_entry
            candidate_entry.refcount = 1
            candidate_entry.last_used = time.monotonic()
            return WorkerDBLease(cls, key, candidate_entry.worker)

    @classmethod
    def release(cls, key: tuple[str, str]) -> None:
        with cls._lock:
            entry = cls._entries.get(key)
            if entry is None or entry.closed:
                return
            if entry.refcount > 0:
                entry.refcount -= 1
            entry.last_used = time.monotonic()

    @classmethod
    def force_close(cls, file_name: str, *, profile: Optional[str] = None) -> None:
        abs_path = os.path.abspath(file_name)
        with cls._lock:
            keys = [
                key
                for key in cls._entries
                if key[0] == abs_path and (profile is None or key[1] == str(profile))
            ]
            entries = [cls._entries.pop(key) for key in keys]
        for entry in entries:
            entry.closed = True
            entry.worker.close()

    @classmethod
    def reap_idle(cls, max_idle_seconds: float) -> int:
        cutoff = time.monotonic() - max(0.0, float(max_idle_seconds))
        with cls._lock:
            keys = [
                key
                for key, entry in cls._entries.items()
                if entry.refcount <= 0 and entry.last_used <= cutoff
            ]
            entries = [cls._entries.pop(key) for key in keys]
        for entry in entries:
            entry.closed = True
            entry.worker.close()
        return len(entries)

    @classmethod
    def close_all(cls) -> None:
        with cls._lock:
            entries = list(cls._entries.values())
            cls._entries.clear()
        for entry in entries:
            entry.closed = True
            entry.worker.close()
