"""Synchronous facade over :class:`sqlite_worker.SqliteWorker`.

``SqliteWorker`` runs a single background thread that owns the actual
``sqlite3.Connection`` and consumes a queue of SQL statements. Writes are
fire-and-forget unless we issue a synchronous barrier; ``SELECT`` statements
can be awaited to collect their result rows. This module wraps the worker
with a small connection-style API so that the project can speak to SQLite
through ``SqliteWorker`` exclusively, without importing ``sqlite3`` directly
in application code.

Notes
-----
* Errors raised by ``SqliteWorker`` for write statements are stored against an
  unreachable token and are silently logged inside the worker. Errors raised
  for ``SELECT`` statements are surfaced through :meth:`WorkerDB.fetch_all`
  and :meth:`WorkerDB.fetch_one`.
* ``SqliteWorker`` only auto-commits when its internal queue drains (or every
  ``max_count`` statements). :meth:`WorkerDB.commit` issues a synchronous
  ``SELECT`` barrier and yields to the worker thread so that the post-execute
  ``conn.commit()`` lands before another connection tries to read the file.
* ``SqliteWorker`` buffers full ``SELECT`` result sets; iteration helpers
  expose the same API but do not stream from the underlying cursor.
"""

from __future__ import annotations

import inspect
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional, Sequence

from tm1_git_py.sqlite_worker import SqliteWorker


_DEFAULT_MAX_QUEUE_SIZE = 65_536


def _sqlite_worker_kwargs(
    *,
    max_queue_size: int,
    max_count: int,
) -> dict[str, Any]:
    """Build kwargs for :class:`SqliteWorker` supported by the installed package."""
    params = inspect.signature(SqliteWorker.__init__).parameters
    kwargs: dict[str, Any] = {}
    if "max_queue_size" in params:
        kwargs["max_queue_size"] = max_queue_size
    if "max_count" in params:
        kwargs["max_count"] = max_count
    return kwargs


class WorkerDB:
    """Connection-style facade around a single :class:`SqliteWorker`."""

    def __init__(
        self,
        file_name: str,
        *,
        execute_init: Sequence[str] = (),
        max_queue_size: int = _DEFAULT_MAX_QUEUE_SIZE,
        max_count: int = 50,
    ) -> None:
        self._file_name = file_name
        init_sql = tuple(execute_init)
        worker_kw = _sqlite_worker_kwargs(
            max_queue_size=max_queue_size,
            max_count=max_count,
        )
        self._worker = SqliteWorker(file_name, **worker_kw)
        for sql in init_sql:
            self.execute(sql)
        if init_sql:
            self.drain()

    @property
    def file_name(self) -> str:
        return self._file_name

    def close(self) -> None:
        """Stop the worker thread and commit pending writes."""
        self._worker.close()

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> None:
        """Queue a write or DDL statement on the worker."""
        normalized_sql = self._normalize_sql(sql)
        token = self._worker.execute(normalized_sql, list(params) if params is not None else [])
        if token is not None:
            # Caller used execute() for a SELECT but did not request results;
            # drain the token so it does not pile up in the worker's results map.
            result = self._await_select_result(token)
            if isinstance(result, BaseException):
                raise result

    def execute_sync(self, sql: str, params: Optional[Sequence[Any]] = None) -> None:
        """Execute a statement and raise worker-thread errors synchronously."""
        normalized_sql = self._normalize_sql(sql)
        token = self._worker.execute(
            normalized_sql,
            list(params) if params is not None else [],
            always_return_token=True,
        )
        result = self._await_select_result(token)
        if isinstance(result, BaseException):
            raise result

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        """Execute ``rows`` as one batched operation on the worker thread."""
        normalized_sql = self._normalize_sql(sql)
        token = self._worker.executemany(
            normalized_sql,
            [list(row) for row in rows],
            always_return_token=True,
        )
        result = self._await_select_result(token)
        if isinstance(result, BaseException):
            raise result

    def fetch_all(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
    ) -> list[tuple]:
        """Run a ``SELECT`` and return all rows as a list of tuples."""
        normalized_sql = self._normalize_sql(sql)
        token = self._worker.execute(normalized_sql, list(params) if params is not None else [])
        if token is None:
            # self.barrier()
            return []
        result = self._await_select_result(token)
        if isinstance(result, BaseException):
            raise result
        if result is None:
            return []
        return [tuple(row) for row in result]

    def fetch_one(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
    ) -> Optional[tuple]:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def fetch_iter(
        self,
        sql: str,
        params: Optional[Sequence[Any]] = None,
    ) -> Iterator[tuple]:
        for row in self.fetch_all(sql, params):
            yield row

    def commit(self) -> None:
        """Compatibility wrapper for callers that expect a connection-like API."""
        self.drain()

    def drain(self) -> None:
        """Synchronously wait for queued work to be applied by the worker."""
        self.barrier()
        # The worker thread runs ``conn.commit()`` *after* notifying SELECT
        # results when its queue drains. Yield briefly so that commit lands
        # before another connection tries to read this file.
        deadline = time.monotonic() + 0.5
        while self._worker.queue_size > 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        time.sleep(0.005)

    def barrier(self) -> None:
        """Wait until the worker has executed everything queued so far."""
        token = self._worker.execute("select 1", [])
        if token is None:
            return
        result = self._await_select_result(token)
        if isinstance(result, BaseException):
            raise result

    @staticmethod
    def _normalize_sql(sql: str) -> str:
        """Strip leading whitespace from SQL.

        ``SqliteWorker`` decides whether a query returns rows by checking
        ``query.lower().startswith("select")`` and silently treats statements
        with leading whitespace or newlines as fire-and-forget writes. We
        normalize here so a multi-line ``SELECT`` is still recognized as one.
        """
        return sql.lstrip()

    def _await_select_result(self, token: str) -> Any:
        """Wait for ``SELECT`` results without racing :meth:`SqliteWorker.fetch_results`.

        ``SqliteWorker.fetch_results`` reads ``_select_events[token]`` and only
        waits if the worker has already inserted an entry, so callers can
        observe ``None`` when they ask for a result before the worker thread
        has even pulled the query from the queue. We pre-register the same
        :class:`threading.Event` the worker would create with ``setdefault``
        and wait on it directly so we do not miss the notification.
        """
        worker = self._worker
        event = worker._select_events.setdefault(token, threading.Event())
        event.wait()
        with worker._lock:
            return worker._results.pop(token, None)
