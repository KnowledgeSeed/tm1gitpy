from __future__ import annotations

import logging
import hashlib
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Callable, Any
from tqdm import tqdm


class ProgressScope(str, Enum):
    TOTAL = "TOTAL"
    WORKER = "WORKER"


class ProgressUnit(str, Enum):
    LINE = "line"
    BYTE = "Byte"


class ProgressKind(str, Enum):
    START = "start"
    UPDATE = "update"
    COMPLETE = "complete"


def _default_worker_id() -> str:
    raw = f"{os.getpid()}:{threading.get_ident()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProgressEvent:
    kind: ProgressKind
    scope: ProgressScope
    unit: ProgressUnit
    worker_id: str
    current: Optional[int] = None
    current_delta: Optional[int] = None
    total: Optional[int] = None
    path: Optional[str] = None
    message: Optional[str] = None
    update_total: bool = False
    timestamp_ns: int = 0

    @staticmethod
    def make(
        *,
        kind: ProgressKind,
        scope: ProgressScope,
        unit: ProgressUnit,
        current: Optional[int] = None,
        current_delta: Optional[int] = None,
        total: Optional[int] = None,
        path: Optional[str] = None,
        worker_id: Optional[str] = None,
        message: Optional[str] = None,
        update_total: bool = False,
    ) -> "ProgressEvent":
        if not isinstance(kind, ProgressKind):
            raise TypeError("ProgressEvent.make kind must be ProgressKind")
        if not isinstance(scope, ProgressScope):
            raise TypeError("ProgressEvent.make scope must be ProgressScope")
        if not isinstance(unit, ProgressUnit):
            raise TypeError("ProgressEvent.make unit must be ProgressUnit")
        has_current = current is not None
        has_delta = current_delta is not None
        if has_current == has_delta:
            raise ValueError("ProgressEvent.make requires exactly one of current or current_delta")
        resolved_worker_id = str(worker_id) if worker_id else _default_worker_id()
        return ProgressEvent(
            kind=kind,
            scope=scope,
            unit=unit,
            worker_id=resolved_worker_id,
            current=current,
            current_delta=current_delta,
            total=total,
            path=path,
            message=message,
            update_total=bool(update_total),
            timestamp_ns=time.time_ns(),
        )


class ProgressSink(Protocol):
    def on_event(self, event: ProgressEvent) -> None:
        ...

    def close(self) -> None:
        ...


class NoopProgressSink:
    def on_event(self, event: ProgressEvent) -> None:
        _ = event
        return

    def close(self) -> None:
        return


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
        parts = [event.kind.value, event.scope.value]
        if event.message:
            parts.append(f"message={event.message}")
        if event.path:
            parts.append(f"path={event.path}")
        if event.current is not None:
            parts.append(f"current={event.current}")
        if event.current_delta is not None:
            parts.append(f"current_delta={event.current_delta}")
        if event.total is not None:
            parts.append(f"total={event.total}")
        parts.append(f"unit={event.unit.value}")
        parts.append(f"worker_id={event.worker_id}")
        self._logger.log(self._level, "progress | %s", " ".join(parts))

    def close(self) -> None:
        return


class TqdmProgressSink:
    def __init__(
        self,
        *,
        worker_count: int,
        base_position: int = 0,
        leave: bool = False,
    ):
        self._lock = threading.Lock()
        self.worker_count = max(1, int(worker_count))
        self.slot_height = self.worker_count + 1
        self.base_position = max(0, int(base_position))
        self.worker_bar_dict: dict[str, Any] = {}
        self._worker_bar_index: dict[str, int] = {}
        self._worker_bars: list[Any] = []
        self._total_bar = None
        self._total_desc = "Total"
        self._leave = bool(leave)

        if tqdm is not None and sys.stderr.isatty():
            terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
            self._total_bar = tqdm(
                total=1,
                desc="Total",
                unit="item",
                unit_scale=False,
                unit_divisor=1024,
                leave=self._leave,
                dynamic_ncols=True,
                ncols=terminal_width,
                position=self.base_position,
            )
            for idx in range(self.worker_count):
                worker_bar = tqdm(
                    total=1,
                    desc=f"Worker {idx}",
                    unit="item",
                    leave=self._leave,
                    dynamic_ncols=True,
                    ncols=terminal_width,
                    position=self.base_position + 1 + idx,
                )
                self._worker_bars.append(worker_bar)
        else:
            self._worker_bars = [None] * self.worker_count

    def _render_worker(self, worker_bar: Any, event: ProgressEvent) -> None:
        if worker_bar is None:
            return
        worker_bar.unit = "B" if event.unit == ProgressUnit.BYTE else "item"
        worker_bar.unit_scale = event.unit == ProgressUnit.BYTE
        worker_bar.unit_divisor = 1024
        existing_total = max(1, int(worker_bar.total or 1))
        if event.update_total:
            if event.current is not None:
                target_total = max(1, int(event.current))
            else:
                target_total = max(1, existing_total + int(event.current_delta or 0))
        else:
            target_total = max(1, int(event.total)) if event.total is not None else existing_total
        if int(worker_bar.total or 0) != target_total:
            worker_bar.reset(total=target_total)
        if event.current is not None:
            worker_bar.n = min(max(0, int(event.current)), target_total)
        else:
            worker_bar.n = min(max(0, int(worker_bar.n) + int(event.current_delta or 0)), target_total)
        base_message = str(event.message or f"{event.kind.value} {event.scope.value}")
        text = str(event.path or "").strip()
        if text:
            try:
                text = os.path.relpath(os.path.abspath(text), os.getcwd())
            except Exception:
                text = str(event.path)
            text = f"{base_message}: {text}"
        else:
            text = base_message
        worker_bar.set_description_str(text, refresh=False)
        worker_bar.refresh()

    def _render_total(self, event: ProgressEvent) -> None:
        if self._total_bar is None:
            return
        self._total_bar.unit = "B" if event.unit == ProgressUnit.BYTE else "item"
        self._total_bar.unit_scale = event.unit == ProgressUnit.BYTE
        self._total_bar.unit_divisor = 1024
        existing_total = max(1, int(self._total_bar.total or 1))
        if event.update_total:
            if event.current is not None:
                target_total = max(1, int(event.current))
            else:
                target_total = max(1, existing_total + int(event.current_delta or 0))
        else:
            target_total = max(1, int(event.total)) if event.total is not None else existing_total
        if int(self._total_bar.total or 0) != target_total:
            self._total_bar.reset(total=target_total)
        if event.current is not None:
            self._total_bar.n = min(max(0, int(event.current)), target_total)
        else:
            self._total_bar.n = min(max(0, int(self._total_bar.n) + int(event.current_delta or 0)), target_total)
        desc = str(event.message) if event.message else f"{self._total_desc} {event.kind.value} {event.scope.value} {int(self._total_bar.n)}/{int(self._total_bar.total or target_total)}"
        self._total_bar.set_description_str(desc, refresh=False)
        self._total_bar.refresh()

    def _resolve_worker_bar(self, event: ProgressEvent) -> Any:
        worker_id = str(event.worker_id) if event.worker_id is not None else None
        if worker_id is not None and worker_id in self.worker_bar_dict:
            return self.worker_bar_dict[worker_id]
        if worker_id is not None and len(self.worker_bar_dict) < len(self._worker_bars):
            for idx, bar in enumerate(self._worker_bars):
                if idx not in self._worker_bar_index.values():
                    self.worker_bar_dict[worker_id] = bar
                    self._worker_bar_index[worker_id] = idx
                    return bar
        if not self._worker_bars:
            return None
        return self._worker_bars[0]

    def _release_worker_if_final(self, event: ProgressEvent) -> None:
        worker_id = str(event.worker_id) if event.worker_id is not None else None
        if worker_id is None:
            return
        if event.current is None or event.total is None:
            return
        if event.kind == ProgressKind.COMPLETE or int(event.current) == int(event.total):
            worker_bar = self.worker_bar_dict.get(worker_id)
            if worker_bar is not None:
                worker_bar.reset(total=1)
                worker_bar.n = 0
                worker_bar.set_description_str("", refresh=False)
                worker_bar.refresh()
            self.worker_bar_dict.pop(worker_id, None)
            self._worker_bar_index.pop(worker_id, None)

    def on_event(self, event: ProgressEvent) -> None:
        with self._lock:
            if event.scope == ProgressScope.TOTAL:
                self._render_total(event)
                return
            worker_bar = self._resolve_worker_bar(event)
            self._render_worker(worker_bar, event)
            self._release_worker_if_final(event)

    def close(self) -> None:
        with self._lock:
            for worker_bar in self._worker_bars:
                if worker_bar is not None:
                    worker_bar.close()
            self._worker_bars = []
            if self._total_bar is not None:
                self._total_bar.close()
                self._total_bar = None
