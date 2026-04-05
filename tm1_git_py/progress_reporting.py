from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Protocol, Callable, Any


@dataclass(frozen=True)
class ProgressEvent:
    kind: str
    scope: str
    current: Optional[int] = None
    total: Optional[int] = None
    unit: Optional[str] = None
    activity: Optional[str] = None
    path: Optional[str] = None
    worker_slot: Optional[int] = None
    message: Optional[str] = None
    timestamp_ns: int = 0

    @staticmethod
    def make(
        *,
        kind: str,
        scope: str,
        current: Optional[int] = None,
        total: Optional[int] = None,
        unit: Optional[str] = None,
        activity: Optional[str] = None,
        path: Optional[str] = None,
        worker_slot: Optional[int] = None,
        message: Optional[str] = None,
    ) -> "ProgressEvent":
        return ProgressEvent(
            kind=kind,
            scope=scope,
            current=current,
            total=total,
            unit=unit,
            activity=activity,
            path=path,
            worker_slot=worker_slot,
            message=message,
            timestamp_ns=time.time_ns(),
        )


class ProgressSink(Protocol):
    def on_event(self, event: ProgressEvent) -> None:
        ...

    def close(self) -> None:
        ...


class CompositeProgressSink:
    def __init__(self, sinks: list[ProgressSink]):
        self._sinks = [sink for sink in sinks if sink is not None]

    def on_event(self, event: ProgressEvent) -> None:
        for sink in self._sinks:
            sink.on_event(event)

    def close(self) -> None:
        for sink in self._sinks:
            close_fn = getattr(sink, "close", None)
            if callable(close_fn):
                close_fn()


class CallbackProgressSink:
    def __init__(self, callback: Any):
        self._callback_obj = callback
        self._callable: Optional[Callable[[ProgressEvent], None]] = None
        if callable(callback):
            self._callable = callback
        elif hasattr(callback, "on_progress") and callable(callback.on_progress):
            self._callable = callback.on_progress
        else:
            raise ValueError("CallbackProgressSink requires a callable or an object with on_progress(event).")

    def on_event(self, event: ProgressEvent) -> None:
        if self._callable is not None:
            self._callable(event)

    def close(self) -> None:
        return


class LoggingProgressSink:
    def __init__(self, logger: logging.Logger, level: int = logging.DEBUG):
        self._logger = logger
        self._level = level

    def on_event(self, event: ProgressEvent) -> None:
        parts = [event.kind, event.scope]
        if event.activity:
            parts.append(f"activity={event.activity}")
        if event.path:
            parts.append(f"path={event.path}")
        if event.current is not None:
            parts.append(f"current={event.current}")
        if event.total is not None:
            parts.append(f"total={event.total}")
        if event.unit:
            parts.append(f"unit={event.unit}")
        if event.worker_slot is not None:
            parts.append(f"worker={event.worker_slot}")
        self._logger.log(self._level, "progress | %s", " ".join(parts))

    def close(self) -> None:
        return
