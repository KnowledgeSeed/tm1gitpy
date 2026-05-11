"""Plain-sqlite content hash calculator for export and deserialize pipelines.

This module computes ``(row_count, content_hash)`` signatures for groups in a
``ModelStore`` SQLite database using direct, read-only ``sqlite3`` connections.
It exists so pipelines can compute signatures without going through the
``SqliteWorker`` path that the rest of the writer path uses, which makes it safe
to fan out across worker threads (or processes) that all read the same SQLite
file. Chunk hashing runs in a :class:`concurrent.futures.ProcessPoolExecutor`
when ``max_workers > 1``; callers should perform ``ModelStore`` writes and
``commit_group_content_signature`` on the owning thread.

Algorithm
---------
For each chunk of rows ordered by identity (using the same ordering and
projection as :func:`tm1_git_py.db.model_store._identity_chunk_query_for_type`):

* a SHA-256 hasher accumulates ``identity_line\n`` for every row, where the
  identity line uses the formatting of
  :func:`tm1_git_py.db.model_store._identity_line_for_type`.

The chunk results are then combined by :func:`_combine_chunk_hashes` using the
``sha256-tree-v1`` digest format stored on ``groups.content_hash``.
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing
import os
import sqlite3
import time
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Iterable, Optional
from urllib.parse import quote

from tm1_git_py.db.model_store import (
    DEFAULT_HASH_FETCH_BATCH_SIZE,
    DEFAULT_PARALLEL_HASH_CHUNK_SIZE,
    ModelStore,
    _identity_chunk_query_for_type,
    _identity_line_for_type,
)
from tm1_git_py.reporting.progress_reporting import NoopProgressSink, ProgressEvent, ProgressSink
from tm1_git_py.internal.process_pool import (
    dispose_process_pool,
    ignore_sigint_in_worker,
    process_pool_executor_kwargs,
)


logger = logging.getLogger(__name__)


_OBJECT_TYPE_TO_TABLE = {
    "element": "element_objects",
    "elements": "element_objects",
    "edge": "edge_objects",
    "edges": "edge_objects",
    "subset": "subset_objects",
    "subsets": "subset_objects",
}


def _normalize_object_type(object_type: str) -> str:
    normalized = str(object_type).strip().lower()
    if normalized not in _OBJECT_TYPE_TO_TABLE:
        raise ValueError(
            f"Unsupported group object type for content hash calculation: '{object_type}'"
        )
    return normalized


def _payload_table_for(normalized: str) -> str:
    return _OBJECT_TYPE_TO_TABLE[normalized]


def _open_readonly_connection(db_path: str, *, busy_timeout_ms: int = 30_000) -> sqlite3.Connection:
    """Open a plain ``sqlite3`` read-only connection suitable for hash workers."""
    quoted_path = quote(os.path.abspath(db_path))
    conn = sqlite3.connect(f"file:{quoted_path}?mode=ro", uri=True, isolation_level=None)
    conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


def _row_count(conn: sqlite3.Connection, payload_table: str, group_id: int) -> int:
    cursor = conn.execute(
        f"SELECT COUNT(*) FROM {payload_table} WHERE group_id=?",
        (int(group_id),),
    )
    row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def _hash_chunk(
    conn: sqlite3.Connection,
    *,
    payload_table: str,
    normalized: str,
    group_id: int,
    chunk_offset: int,
    chunk_limit: int,
    fetch_batch_size: int,
) -> tuple[int, str]:
    sql = _identity_chunk_query_for_type(payload_table, normalized)
    hasher = hashlib.sha256()
    row_count = 0
    local_offset = max(0, int(chunk_offset))
    remaining = max(0, int(chunk_limit))
    page_size = max(1, int(fetch_batch_size))
    while remaining > 0:
        page_limit = min(page_size, remaining)
        cursor = conn.execute(sql, (int(group_id), int(page_limit), int(local_offset)))
        rows = cursor.fetchall()
        if not rows:
            break
        for row in rows:
            line = _identity_line_for_type(normalized, tuple(row)) + "\n"
            hasher.update(line.encode("utf-8"))
            row_count += 1
        fetched = len(rows)
        local_offset += fetched
        remaining -= fetched
        if fetched < page_limit:
            break
    return row_count, hasher.hexdigest()


def _hash_chunk_worker(job: dict[str, Any], progress_sink: ProgressSink) -> tuple[int, int, str]:
    progress_sink.on_event(ProgressEvent.worker_line(current=0, total=1, message=f"Hashing group {job['group_id']} chunk {job['chunk_idx']}"))
    conn = _open_readonly_connection(
        str(job["db_path"]),
        busy_timeout_ms=int(job.get("busy_timeout_ms", 30_000)),
    )
    try:
        chunk_rows, chunk_hash = _hash_chunk(
            conn,
            payload_table=str(job["payload_table"]),
            normalized=str(job["normalized"]),
            group_id=int(job["group_id"]),
            chunk_offset=int(job["chunk_offset"]),
            chunk_limit=int(job["chunk_limit"]),
            fetch_batch_size=int(job["fetch_batch_size"]),
        )
        return int(job["chunk_idx"]), chunk_rows, chunk_hash
    finally:
        progress_sink.on_event(ProgressEvent.worker_line(current=1, total=1, message=f"Hashing group {job['group_id']} chunk {job['chunk_idx']}"))
        conn.close()


def _combine_chunk_hashes(
    *,
    algo: str,
    empty_hash: str,
    normalized: str,
    total_rows: int,
    chunk_results: Iterable[tuple[int, int, str]],
) -> str:
    """Combine per-chunk ``(chunk_idx, row_count, chunk_hash)`` triples."""
    results = list(chunk_results)
    if total_rows == 0:
        return empty_hash
    hasher = hashlib.sha256()
    hasher.update((algo + "\n").encode("utf-8"))
    hasher.update((normalized + "\n").encode("utf-8"))
    hasher.update((str(total_rows) + "\n").encode("utf-8"))
    for chunk_idx, chunk_rows, chunk_hash in sorted(results, key=lambda item: item[0]):
        hasher.update(f"{chunk_idx}:{chunk_rows}:{chunk_hash}\n".encode("utf-8"))
    return hasher.hexdigest()


def _build_hash_jobs(
    *,
    db_path: str,
    payload_table: str,
    normalized: str,
    group_id: int,
    total_rows: int,
    chunk_size: int,
    fetch_batch_size: int,
    busy_timeout_ms: int,
) -> list[dict[str, Any]]:
    return [
        {
            "db_path": db_path,
            "payload_table": payload_table,
            "normalized": normalized,
            "group_id": group_id,
            "chunk_idx": idx,
            "chunk_offset": int(chunk_offset),
            "chunk_limit": int(min(chunk_size, total_rows - int(chunk_offset))),
            "fetch_batch_size": fetch_batch_size,
            "busy_timeout_ms": busy_timeout_ms,
        }
        for idx, chunk_offset in enumerate(range(0, total_rows, chunk_size))
    ]


class ContentHashCalculator:
    """Plain-sqlite group content hash calculator with an optional process pool."""

    def __init__(
        self,
        *,
        db_path: str,
        max_workers: Optional[int] = None,
        process_pool: Optional[ProcessPoolExecutor] = None,
        chunk_size: int = DEFAULT_PARALLEL_HASH_CHUNK_SIZE,
        fetch_batch_size: int = DEFAULT_HASH_FETCH_BATCH_SIZE,
        busy_timeout_ms: int = 30_000,
        progress_sink: Optional[ProgressSink] = None,
    ) -> None:
        self.db_path = str(db_path)
        self._max_workers_explicit = max_workers is not None
        self.max_workers = max(1, int(max_workers or 1))
        self.chunk_size = max(1, int(chunk_size))
        self.fetch_batch_size = max(1, int(fetch_batch_size))
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._process_pool: Optional[ProcessPoolExecutor] = None
        self._process_pool_unavailable = False
        self._calculate_lock = threading.Lock()
        self.progress_sink = progress_sink if progress_sink is not None else NoopProgressSink()

    def __enter__(self) -> "ContentHashCalculator":
        self._ensure_process_pool()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is KeyboardInterrupt:
            self.close(wait=False)
            return
        self.close()

    @property
    def use_parallel(self) -> bool:
        return (
            self.max_workers > 1
            and not self._process_pool_unavailable
            and self._process_pool is not None
        )

    def close(self, *, wait: bool = True) -> None:
        pool = self._process_pool
        if pool is None:
            return
        self._process_pool = None
        dispose_process_pool(
            pool,
            mode="graceful_bounded" if wait else "aggressive",
            log=True,
        )

    def calculate_group_content_signature(
        self,
        *,
        group_id: int,
        object_type: str,
    ) -> tuple[int, str]:
        """Compute ``(row_count, content_hash)`` for a single group.

        If this calculator was initialized with ``max_workers > 1``, chunks are
        submitted to its process pool. Each process opens its own read-only
        ``sqlite3`` connection; no connection object is shared with workers.
        """
        with self._calculate_lock:
            return self._calculate_group_content_signature(
                group_id=group_id,
                object_type=object_type,
            )

    def await_consistency(
        self,
        *,
        group_id: int,
        object_type: str,
        expected_count: int,
        timeout: float = 5.0,
    ) -> None:
        """Poll read-only row count until it matches ``expected_count`` or ``timeout`` elapses.

        Sleeps 0.1s between checks. Raises :class:`ValueError` on timeout.
        """
        normalized = _normalize_object_type(object_type)
        payload_table = _payload_table_for(normalized)
        deadline = time.monotonic() + max(0.0, float(timeout))
        expected = int(expected_count)
        with self._calculate_lock:
            conn = _open_readonly_connection(self.db_path, busy_timeout_ms=self.busy_timeout_ms)
            try:
                while True:
                    total_rows = _row_count(conn, payload_table, int(group_id))
                    if total_rows == expected:
                        return True
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            f"Consistency timeout for group_id={int(group_id)} object_type={object_type!r}: "
                            f"expected {expected} rows, last saw {total_rows} "
                            f"after {float(timeout)}s"
                        )
                    time.sleep(0.1)
            finally:
                conn.close()
        return False

    def _calculate_group_content_signature(
        self,
        *,
        group_id: int,
        object_type: str,
    ) -> tuple[int, str]:
        normalized = _normalize_object_type(object_type)
        payload_table = _payload_table_for(normalized)

        conn = _open_readonly_connection(self.db_path, busy_timeout_ms=self.busy_timeout_ms)
        try:
            total_rows = _row_count(conn, payload_table, int(group_id))
            if total_rows == 0:
                return 0, ModelStore.EMPTY_CONTENT_HASH

            jobs = self._build_jobs(
                payload_table=payload_table,
                normalized=normalized,
                group_id=int(group_id),
                total_rows=total_rows,
            )
            use_parallel = self.use_parallel and len(jobs) > 1
            run_chunked_serial = self._max_workers_explicit and self.max_workers == 1
            if use_parallel:
                try:
                    chunk_results = self._calculate_parallel(jobs)
                except (OSError, NotImplementedError):
                    use_parallel = False

            if not use_parallel and run_chunked_serial:
                chunk_results = self._calculate_serial(jobs)
            elif not use_parallel:
                chunk_rows, chunk_hash = _hash_chunk(
                    conn,
                    payload_table=payload_table,
                    normalized=normalized,
                    group_id=int(group_id),
                    chunk_offset=0,
                    chunk_limit=total_rows,
                    fetch_batch_size=self.fetch_batch_size,
                )
                chunk_results = [(0, chunk_rows, chunk_hash)]
        finally:
            conn.close()

        row_count = sum(chunk_rows for _, chunk_rows, _ in chunk_results)
        content_hash = _combine_chunk_hashes(
            algo=ModelStore.PARALLEL_HASH_ALGO,
            empty_hash=ModelStore.EMPTY_CONTENT_HASH,
            normalized=normalized,
            total_rows=row_count,
            chunk_results=chunk_results,
        )
        return row_count, content_hash

    def _build_jobs(
        self,
        *,
        payload_table: str,
        normalized: str,
        group_id: int,
        total_rows: int,
    ) -> list[dict[str, Any]]:
        return _build_hash_jobs(
            db_path=self.db_path,
            payload_table=payload_table,
            normalized=normalized,
            group_id=group_id,
            total_rows=total_rows,
            chunk_size=self.chunk_size,
            fetch_batch_size=self.fetch_batch_size,
            busy_timeout_ms=self.busy_timeout_ms,
        )

    def _calculate_serial(self, jobs: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
        return [_hash_chunk_worker(job, self.progress_sink) for job in jobs]

    def _ensure_process_pool(self) -> None:
        if self.max_workers <= 1 or self._process_pool is not None or self._process_pool_unavailable:
            return
        try:
            multiprocessing.freeze_support()
            self._process_pool = ProcessPoolExecutor(
                **process_pool_executor_kwargs(
                    max_workers=self.max_workers,
                    initializer=ignore_sigint_in_worker,
                ),
            )
        except (OSError, NotImplementedError):
            self._process_pool_unavailable = True
            logger.warning(
                "ProcessPoolExecutor unavailable for content hash calculation; using serial mode",
                exc_info=True,
            )

    def _calculate_parallel(self, jobs: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
        self._ensure_process_pool()
        if self._process_pool is None:
            raise RuntimeError("Process pool is not available")
        try:
            futures = [self._process_pool.submit(_hash_chunk_worker, job, self.progress_sink) for job in jobs]
            return [future.result() for future in as_completed(futures)]
        except (OSError, NotImplementedError):
            self._process_pool_unavailable = True
            self.close()
            logger.warning(
                "ProcessPoolExecutor unavailable for content hash calculation; using serial mode",
                exc_info=True,
            )
            raise


def calculate_group_content_signature(
    *,
    db_path: str,
    group_id: int,
    object_type: str,
    chunk_size: int = DEFAULT_PARALLEL_HASH_CHUNK_SIZE,
    fetch_batch_size: int = DEFAULT_HASH_FETCH_BATCH_SIZE,
    busy_timeout_ms: int = 30_000,
    max_workers: Optional[int] = None,
    count: Optional[int] = None,
) -> tuple[int, str]:
    """Convenience wrapper that owns a short-lived :class:`ContentHashCalculator`.

    When ``count`` is set, :meth:`ContentHashCalculator.ensure_consistency` runs first.
    """
    with ContentHashCalculator(
        db_path=db_path,
        max_workers=max_workers,
        chunk_size=chunk_size,
        fetch_batch_size=fetch_batch_size,
        busy_timeout_ms=busy_timeout_ms,
    ) as calculator:
        if count is not None:
            calculator.await_consistency(
                group_id=group_id,
                object_type=object_type,
                expected_count=int(count),
            )
        return calculator.calculate_group_content_signature(
            group_id=group_id,
            object_type=object_type,
        )


def store_group_content_signature(
    *,
    store: ModelStore,
    group_id: int,
    chunk_size: int = DEFAULT_PARALLEL_HASH_CHUNK_SIZE,
    fetch_batch_size: int = DEFAULT_HASH_FETCH_BATCH_SIZE,
    busy_timeout_ms: int = 30_000,
    max_workers: Optional[int] = None,
    count: int,
) -> tuple[int, str]:
    """Compute ``(row_count, content_hash)`` for a group using the store's object type."""
    normalized, _, _ = store.resolve_parallel_hash_inputs(group_id)
    return calculate_group_content_signature(
        db_path=store.db_path,
        group_id=group_id,
        object_type=normalized,
        chunk_size=chunk_size,
        fetch_batch_size=fetch_batch_size,
        busy_timeout_ms=busy_timeout_ms,
        max_workers=max_workers,
        count=count,
    )


__all__ = [
    "ContentHashCalculator",
    "calculate_group_content_signature",
    "store_group_content_signature",
]
