import logging
import os
import re
import shutil
import sys
import threading
from itertools import count
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional
import ijson
import orjson
from tqdm import tqdm
from tm1_git_py.model import Edge
from tm1_git_py.model.chore import Chore
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.element import Element
from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.model.model import Model
from tm1_git_py.model.model_store import ModelStore
from tm1_git_py.model.store_backed_sequence import StoreBackedSequence
from tm1_git_py.model.process import Process
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.subset import Subset
from tm1_git_py.model.task import Task
from tm1_git_py.model.ti import TI
from tm1_git_py.progress_reporting import (
    ProgressEvent,
    ProgressSink,
)


logger = logging.getLogger(__name__)
DESERIALIZE_PROGRESS_EVERY = 100_000


def _default_max_workers() -> int:
    return max(1, ((os.cpu_count() or 1) // 2) + 1)


class TqdmDeserializerProgressSink:
    _SLOT_HEIGHT = 5
    _slot_height = 5
    _slot_lock = threading.Lock()
    _active_slots: set[int] = set()

    def __init__(
        self,
        root_dir: str,
        *,
        enable_fallback_logs: bool = True,
        preferred_slot_index: Optional[int] = None,
        worker_count: int = 4,
    ):
        self._root_dir = str(root_dir)
        self._enable_fallback_logs = enable_fallback_logs
        self._tracked_dirs = ("dimensions", "cubes", "processes", "chores")
        self._lock = threading.Lock()
        self._processed_paths: set[str] = set()
        self._file_sizes: dict[str, int] = {}
        self._file_reported_bytes: dict[str, int] = {}
        self._total_bytes = self._compute_total_bytes()
        self._processed_bytes = 0
        self._last_logged_pct = -1
        self._worker_count = max(1, int(worker_count))
        self._slot_index = self._acquire_slot(
            preferred_slot_index,
            required_height=self._worker_count + 1,
        )
        self._base_position = self._slot_index * self._slot_height
        self._worker_file_abs: list[str] = [""] * self._worker_count
        self._worker_file: list[str] = [""] * self._worker_count
        self._worker_activity: list[str] = ["idle"] * self._worker_count
        self._last_status_line: list[str] = [""] * self._worker_count
        self._overall_bar = None
        self._worker_bars = []
        if sys.stderr.isatty() and tqdm is not None:
            terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
            if self._total_bytes > 0:
                self._overall_bar = tqdm(
                    total=self._total_bytes,
                    unit="B",
                    unit_scale=True,
                    desc="Deserializing",
                    leave=False,
                    dynamic_ncols=True,
                    ncols=terminal_width,
                    position=self._base_position,
                )
            for idx in range(self._worker_count):
                worker_bar = tqdm(
                    total=1,
                    unit="B",
                    unit_scale=True,
                    desc="",
                    leave=False,
                    dynamic_ncols=True,
                    ncols=terminal_width,
                    position=self._base_position + 1 + idx,
                )
                self._worker_bars.append(worker_bar)
                worker_bar.set_description_str("", refresh=True)

    @classmethod
    def _acquire_slot(cls, preferred_slot: Optional[int] = None, *, required_height: Optional[int] = None) -> int:
        with cls._slot_lock:
            if required_height is not None:
                cls._slot_height = max(cls._slot_height, max(cls._SLOT_HEIGHT, int(required_height)))
            if preferred_slot is not None:
                preferred = max(0, int(preferred_slot))
                if preferred not in cls._active_slots:
                    cls._active_slots.add(preferred)
                    return preferred
            slot = 0
            while slot in cls._active_slots:
                slot += 1
            cls._active_slots.add(slot)
            return slot

    @classmethod
    def _release_slot(cls, slot_index: int) -> None:
        with cls._slot_lock:
            cls._active_slots.discard(slot_index)

    def _relpath_for_display(self, file_path: str) -> str:
        normalized = os.path.abspath(file_path)
        try:
            return os.path.relpath(normalized, self._root_dir)
        except Exception:
            return normalized

    def _fit_status_line_for_bar(self, status_line: str, worker_slot: int) -> str:
        if worker_slot >= len(self._worker_bars):
            return status_line
        ncols = int(getattr(self._worker_bars[worker_slot], "ncols", 0) or 0)
        max_len = max(30, int(ncols * 0.42)) if ncols > 0 else 80
        if len(status_line) <= max_len:
            return status_line
        if ": " in status_line:
            activity, file_part = status_line.split(": ", 1)
            reserved = len(activity) + 5
            file_max = max(8, max_len - reserved)
            if len(file_part) > file_max:
                file_part = "..." + file_part[-(file_max - 3):]
            return f"{activity}: {file_part}"
        return status_line[: max_len - 3] + "..."

    def _render_status_locked(self, worker_slot: int) -> None:
        if self._worker_file[worker_slot]:
            status_line = f"{self._worker_activity[worker_slot]}: {self._worker_file[worker_slot]}"
        else:
            status_line = self._worker_activity[worker_slot]
        worker_bar = self._worker_bars[worker_slot] if worker_slot < len(self._worker_bars) else None
        if worker_bar is not None:
            status_line = self._fit_status_line_for_bar(status_line, worker_slot)
        if status_line == self._last_status_line[worker_slot]:
            return
        self._last_status_line[worker_slot] = status_line
        if worker_bar is not None:
            worker_bar.set_description_str(status_line, refresh=True)
        elif self._enable_fallback_logs:
            logging.getLogger(__name__).debug("Progress activity %s", status_line)

    def _compute_total_bytes(self) -> int:
        total = 0
        for dirname in self._tracked_dirs:
            base = os.path.join(self._root_dir, dirname)
            if not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    try:
                        normalized = os.path.abspath(file_path)
                        file_size = int(os.path.getsize(file_path))
                        self._file_sizes[normalized] = file_size
                        total += file_size
                    except OSError:
                        continue
        return total

    def _apply_progress_delta_locked(self, delta: int) -> None:
        if delta <= 0:
            return
        self._processed_bytes += delta
        if self._overall_bar is not None:
            self._overall_bar.update(delta)
            return
        if self._total_bytes <= 0:
            return
        pct = int((self._processed_bytes * 100) / self._total_bytes)
        if pct >= self._last_logged_pct + 2 or pct == 100:
            self._last_logged_pct = pct
            if self._enable_fallback_logs:
                logging.getLogger(__name__).debug(
                    "Progress %d%% (%d/%d bytes)",
                    pct,
                    self._processed_bytes,
                    self._total_bytes,
                )

    def on_event(self, event: ProgressEvent) -> None:
        worker_slot = int(event.worker_slot or 0)
        worker_slot = max(0, min(self._worker_count - 1, worker_slot))
        with self._lock:
            if event.scope == "file" and event.path:
                normalized = os.path.abspath(str(event.path))
                self._worker_file_abs[worker_slot] = normalized
                self._worker_file[worker_slot] = self._relpath_for_display(str(event.path))
                if event.total is not None:
                    self._file_sizes[normalized] = max(0, int(event.total))
            elif event.path:
                self._worker_file[worker_slot] = str(event.path)
            if event.activity:
                self._worker_activity[worker_slot] = event.activity

            if event.scope == "overall" and event.kind in ("scope_start", "scope_update"):
                if self._overall_bar is not None:
                    target_total = int(event.total) if event.total is not None else int(self._overall_bar.total or 1)
                    target_total = max(1, target_total)
                    if int(self._overall_bar.total or 0) != target_total:
                        self._overall_bar.reset(total=target_total)
                    if event.current is not None:
                        target_n = min(max(0, int(event.current)), target_total)
                        delta = target_n - int(self._overall_bar.n)
                        if delta > 0:
                            self._overall_bar.update(delta)
                        else:
                            self._overall_bar.n = target_n
                            self._overall_bar.refresh()
            elif event.kind in ("scope_start", "scope_update") and event.current is not None:
                if event.scope == "file":
                    normalized = self._worker_file_abs[worker_slot]
                    if normalized:
                        bounded = max(0, int(event.current))
                        previous = self._file_reported_bytes.get(normalized, 0)
                        if bounded > previous:
                            self._file_reported_bytes[normalized] = bounded
                            self._apply_progress_delta_locked(bounded - previous)
                        if event.total is not None and bounded >= int(event.total):
                            self._processed_paths.add(normalized)
                if worker_slot < len(self._worker_bars):
                    worker_bar = self._worker_bars[worker_slot]
                    if event.unit:
                        worker_bar.unit = event.unit
                        worker_bar.unit_scale = event.unit == "B"
                    target_total = int(event.total) if event.total is not None else int(worker_bar.total or 1)
                    target_total = max(1, target_total)
                    if int(worker_bar.total or 0) != target_total:
                        worker_bar.reset(total=target_total)
                    worker_bar.n = min(max(0, int(event.current)), target_total)
                    worker_bar.refresh()
                self._render_status_locked(worker_slot)
            elif event.scope == "worker":
                self._render_status_locked(worker_slot)

    def apply_external_event(self, event: ProgressEvent) -> None:
        self.on_event(event)

    def start_file(self, file_path: str, *, activity: str = "processing", worker_slot: int = 0) -> None:
        total = 0
        try:
            total = int(os.path.getsize(file_path))
        except OSError:
            total = 0
        self.on_event(
            ProgressEvent.make(
                kind="scope_start",
                scope="file",
                current=0,
                total=max(1, total),
                unit="B",
                activity=activity,
                path=file_path,
                worker_slot=worker_slot,
            )
        )

    def report_file_progress(self, file_path: str, processed_bytes: int, *, worker_slot: int = 0) -> None:
        total = 0
        try:
            total = int(os.path.getsize(file_path))
        except OSError:
            total = max(0, int(processed_bytes))
        self.on_event(
            ProgressEvent.make(
                kind="scope_update",
                scope="file",
                current=max(0, int(processed_bytes)),
                total=max(1, total),
                unit="B",
                activity=self._worker_activity[max(0, min(self._worker_count - 1, int(worker_slot)))],
                path=file_path,
                worker_slot=worker_slot,
            )
        )

    def mark_file_processed(self, file_path: str, *, worker_slot: int = 0) -> None:
        total = 0
        try:
            total = int(os.path.getsize(file_path))
        except OSError:
            total = 0
        self.on_event(
            ProgressEvent.make(
                kind="scope_update",
                scope="file",
                current=max(1, total),
                total=max(1, total),
                unit="B",
                activity="completed",
                path=file_path,
                worker_slot=worker_slot,
            )
        )

    def start_line_activity(
        self,
        label: str,
        *,
        total_lines: int,
        activity: str,
        worker_slot: int = 0,
    ) -> None:
        self.on_event(
            ProgressEvent.make(
                kind="scope_start",
                scope="line",
                current=0,
                total=max(1, int(total_lines)),
                unit="line",
                activity=activity,
                path=label,
                worker_slot=worker_slot,
            )
        )

    def report_line_progress(
        self,
        processed_lines: int,
        *,
        total_lines: Optional[int] = None,
        worker_slot: int = 0,
    ) -> None:
        self.on_event(
            ProgressEvent.make(
                kind="scope_update",
                scope="line",
                current=max(0, int(processed_lines)),
                total=max(1, int(total_lines if total_lines is not None else processed_lines or 1)),
                unit="line",
                activity="processing",
                worker_slot=worker_slot,
            )
        )

    def close(self) -> None:
        with self._lock:
            for worker_bar in self._worker_bars:
                worker_bar.close()
            self._worker_bars = []
            if self._overall_bar is not None:
                self._overall_bar.close()
                self._overall_bar = None
        self._release_slot(self._slot_index)


_DeserializeProgress = TqdmDeserializerProgressSink


def _json_load_text(raw: str) -> Any:
    return orjson.loads(raw)


def _json_load_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as src:
        return _json_load_text(src.read())


def _progress_start(
    progress: Optional[ProgressSink],
    file_path: str,
    activity: str,
    *,
    worker_slot: int = 0,
) -> None:
    if progress is not None:
        total = 0
        try:
            total = int(os.path.getsize(file_path))
        except OSError:
            total = 0
        progress.on_event(
            ProgressEvent.make(
                kind="scope_start",
                scope="file",
                current=0,
                total=max(1, total),
                unit="B",
                activity=activity,
                path=file_path,
                worker_slot=worker_slot,
            )
        )


def _progress_mark(
    progress: Optional[ProgressSink],
    file_path: str,
    *,
    worker_slot: int = 0,
) -> None:
    if progress is not None:
        total = 0
        try:
            total = int(os.path.getsize(file_path))
        except OSError:
            total = 0
        progress.on_event(
            ProgressEvent.make(
                kind="scope_update",
                scope="file",
                current=max(1, total),
                total=max(1, total),
                unit="B",
                activity="completed",
                path=file_path,
                worker_slot=worker_slot,
            )
        )


def _recalculate_group_signature_task(
    *,
    model_root: str,
    group_id: int,
    progress: Optional[ProgressSink],
    progress_scope: str,
    progress_worker_slot: int,
    max_workers: int,
) -> tuple[int, str]:
    store = ModelStore.for_main_dir(model_root)
    total_lines = store.row_count(group_id)
    if progress is not None:
        progress.on_event(
            ProgressEvent.make(
                kind="scope_start",
                scope="line",
                current=0,
                total=max(1, total_lines),
                unit="line",
                activity="recalculating hash",
                path=progress_scope,
                worker_slot=progress_worker_slot,
            )
        )
    def _on_progress(processed: int) -> None:
        if progress is not None:
            progress.on_event(
                ProgressEvent.make(
                    kind="scope_update",
                    scope="line",
                    current=max(0, int(processed)),
                    total=max(1, total_lines),
                    unit="line",
                    activity="recalculating hash",
                    path=progress_scope,
                    worker_slot=progress_worker_slot,
                )
            )

    row_count, content_hash = store.recalculate_group_content_signature_parallel(
        group_id,
        ordered_by_identity=True,
        fetch_batch_size=5_000,
        max_workers=max(1, int(max_workers)),
        progress_callback=_on_progress,
    )
    if progress is not None:
        progress.on_event(
            ProgressEvent.make(
                kind="scope_update",
                scope="line",
                current=max(0, int(row_count)),
                total=max(1, total_lines),
                unit="line",
                activity="recalculating hash",
                path=progress_scope,
                worker_slot=progress_worker_slot,
            )
        )
    return row_count, content_hash


def _hierarchy_has_top_level_key(hierarchy_json_path: str, key: str) -> bool:
    with open(hierarchy_json_path, "rb") as src:
        for prefix, event, value in ijson.parse(src):
            if prefix == "" and event == "map_key" and value == key:
                return True
    return False


def _iter_hierarchy_array_payloads(
    hierarchy_json_path: str,
    key: str,
    *,
    progress: Optional[ProgressSink] = None,
    progress_range: tuple[float, float] = (0.0, 1.0),
) -> Iterator[dict]:
    item_prefix = f"{key}.item"
    emitted = False
    reported_at = 0
    try:
        file_size = int(os.path.getsize(hierarchy_json_path))
    except OSError:
        file_size = 0
    start_fraction = max(0.0, min(1.0, float(progress_range[0])))
    end_fraction = max(0.0, min(1.0, float(progress_range[1])))
    if end_fraction < start_fraction:
        start_fraction, end_fraction = end_fraction, start_fraction

    def _scaled_position(raw_position: int) -> int:
        if file_size <= 0:
            return max(0, int(raw_position))
        ratio = max(0.0, min(1.0, float(raw_position) / float(file_size)))
        scaled_ratio = start_fraction + ((end_fraction - start_fraction) * ratio)
        return int(scaled_ratio * file_size)

    with open(hierarchy_json_path, "rb") as src:
        for index, payload in enumerate(ijson.items(src, item_prefix), start=1):
            if not isinstance(payload, dict):
                raise ValueError(f"Malformed hierarchy json: non-object payload in array '{key}'")
            emitted = True
            if progress is not None and index % 1_000 == 0:
                position = int(src.tell())
                if position > reported_at:
                    progress.on_event(
                        ProgressEvent.make(
                            kind="scope_update",
                            scope="file",
                            current=max(0, int(_scaled_position(position))),
                            total=max(1, file_size),
                            unit="B",
                            activity="reading hierarchy",
                            path=hierarchy_json_path,
                            worker_slot=0,
                        )
                    )
                    reported_at = position
            yield payload
        if progress is not None:
            progress.on_event(
                ProgressEvent.make(
                    kind="scope_update",
                    scope="file",
                    current=max(0, int(_scaled_position(int(src.tell())))),
                    total=max(1, file_size),
                    unit="B",
                    activity="reading hierarchy",
                    path=hierarchy_json_path,
                    worker_slot=0,
                )
            )
    if not emitted and not _hierarchy_has_top_level_key(hierarchy_json_path, key):
        raise ValueError(f"Malformed hierarchy json: key '{key}' not found")


def _append_payloads_in_batches(
    *,
    store: ModelStore,
    group_id: int,
    payloads: Iterable[dict],
    batch_size: int = 100_000,
    progress_label: Optional[str] = None,
    progress_every: int = DESERIALIZE_PROGRESS_EVERY,
) -> int:
    return store.append_payloads(
        group_id=group_id,
        payloads=payloads,
        batch_size=batch_size,
        progress_label=progress_label,
        progress_every=progress_every,
    )


def _subset_source_mtime_ns(subset_dir_path: str) -> int:
    if not os.path.isdir(subset_dir_path):
        return 0
    latest = 0
    for subset_file_name in os.listdir(subset_dir_path):
        if not subset_file_name.endswith(".json"):
            continue
        subset_path = os.path.join(subset_dir_path, subset_file_name)
        try:
            mtime_ns = int(os.stat(subset_path).st_mtime_ns)
        except OSError:
            continue
        if mtime_ns > latest:
            latest = mtime_ns
    return latest


def _ensure_hierarchy_store_groups(
    *,
    hierarchy_json_path: str,
    model_root: str,
    dimension_name: str,
    hierarchy_name: str,
    subset_dir_path: str,
    progress: Optional[ProgressSink] = None,
    max_workers: int = 1,
    hash_slot_counter: Optional[count] = None,
) -> tuple[StoreBackedSequence[Element], StoreBackedSequence[Edge], StoreBackedSequence[Subset]]:
    store = ModelStore.for_main_dir(model_root)
    model_id = store.resolve_model_for_deserialize(model_root)
    elements = StoreBackedSequence.for_elements_sink(
        store=store,
        model_id=model_id,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
    )
    edges = StoreBackedSequence.for_edges_sink(
        store=store,
        model_id=model_id,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
    )
    subsets = StoreBackedSequence.for_subsets_sink(
        store=store,
        model_id=model_id,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
    )
    source_mtime_ns = int(os.stat(hierarchy_json_path).st_mtime_ns) if os.path.exists(hierarchy_json_path) else None
    needs_elements_rebuild = source_mtime_ns is None or elements.source_json_mtime_ns() != source_mtime_ns
    needs_edges_rebuild = source_mtime_ns is None or edges.source_json_mtime_ns() != source_mtime_ns
    subset_source_mtime_ns = _subset_source_mtime_ns(subset_dir_path)
    needs_subsets_rebuild = subsets.source_json_mtime_ns() != subset_source_mtime_ns
    if needs_elements_rebuild and needs_edges_rebuild:
        elements_progress_range = (0.0, 0.5)
        edges_progress_range = (0.5, 1.0)
    elif needs_elements_rebuild:
        elements_progress_range = (0.0, 1.0)
        edges_progress_range = (0.0, 1.0)
    elif needs_edges_rebuild:
        elements_progress_range = (0.0, 1.0)
        edges_progress_range = (0.0, 1.0)
    else:
        elements_progress_range = (0.0, 1.0)
        edges_progress_range = (0.0, 1.0)

    def _enqueue_hash_recalc(group_id: int, scope: str) -> None:
        worker_slot = 0
        if hash_slot_counter is not None:
            worker_slot = next(hash_slot_counter) % max(1, int(max_workers))
        _recalculate_group_signature_task(
            model_root=model_root,
            group_id=group_id,
            progress=progress,
            progress_scope=scope,
            progress_worker_slot=worker_slot,
            max_workers=max_workers,
        )

    if needs_elements_rebuild:
        _progress_start(progress, hierarchy_json_path, "inserting elements")
        elements.replace_with_payloads(())
        _append_payloads_in_batches(
            store=store,
            group_id=elements.group_id,
            payloads=_iter_hierarchy_array_payloads(
                hierarchy_json_path,
                "Elements",
                progress=progress,
                progress_range=elements_progress_range,
            ),
        )
        if source_mtime_ns is not None:
            elements.set_source_json_mtime_ns(source_mtime_ns)
        _enqueue_hash_recalc(elements.group_id, f"{dimension_name}/{hierarchy_name} elements")
    if needs_edges_rebuild:
        _progress_start(progress, hierarchy_json_path, "inserting edges")
        edges.replace_with_payloads(())
        _append_payloads_in_batches(
            store=store,
            group_id=edges.group_id,
            payloads=_iter_hierarchy_array_payloads(
                hierarchy_json_path,
                "Edges",
                progress=progress,
                progress_range=edges_progress_range,
            ),
        )
        if source_mtime_ns is not None:
            edges.set_source_json_mtime_ns(source_mtime_ns)
        _enqueue_hash_recalc(edges.group_id, f"{dimension_name}/{hierarchy_name} edges")

    if needs_subsets_rebuild:
        subsets.replace_with_payloads(())
        if os.path.isdir(subset_dir_path):
            def _subset_payload_iter() -> Iterator[dict]:
                for subset_file_name in sorted(os.listdir(subset_dir_path)):
                    if not subset_file_name.endswith(".json"):
                        subset_artifact_path = os.path.join(subset_dir_path, subset_file_name)
                        _progress_start(progress, subset_artifact_path, "skipping non-json subset artifact")
                        _progress_mark(progress, subset_artifact_path)
                        continue
                    subset_path = os.path.join(subset_dir_path, subset_file_name)
                    _progress_start(progress, subset_path, "inserting subsets")
                    subset_json = _json_load_file(subset_path)
                    _progress_mark(progress, subset_path)
                    yield {
                        "name": subset_json.get("Name") or subset_json.get("name"),
                        "expression": subset_json.get("Expression") or subset_json.get("expression"),
                    }

            _append_payloads_in_batches(
                store=store,
                group_id=subsets.group_id,
                payloads=_subset_payload_iter(),
            )
        subsets.set_source_json_mtime_ns(subset_source_mtime_ns)
        _enqueue_hash_recalc(subsets.group_id, f"{dimension_name}/{hierarchy_name} subsets")
    elif progress is not None and os.path.isdir(subset_dir_path):
        for subset_file_name in sorted(os.listdir(subset_dir_path)):
            _progress_mark(progress, os.path.join(subset_dir_path, subset_file_name))
    if progress is not None and (needs_elements_rebuild or needs_edges_rebuild):
        _progress_mark(progress, hierarchy_json_path)
    elif progress is not None:
        _progress_mark(progress, hierarchy_json_path)
    return elements, edges, subsets



def _handle_long_path(file_path) -> str:
    file_path = os.path.abspath(file_path)

    if os.name == 'nt' and not file_path.startswith("\\\\?\\"):
        if file_path.startswith("\\\\"):
            file_path = Path(file_path[2:])
            file_path = "\\\\?\\UNC\\" /file_path
            return str(file_path)
        else:
            file_path = Path(file_path)
            file_path = "\\\\?\\" / file_path
            return str(file_path)
    return file_path

def deserialize_model(
    dir: str,
    *,
    progress_sink: Optional[ProgressSink] = None,
    max_workers: Optional[int] = None,
) -> tuple[Model, dict[str, str]]:
    logger.debug("Deserializing model from '%s'", dir)
    dir = _handle_long_path(dir)

    dimensions_dir = dir + '/dimensions'
    cubes_dir = dir + '/cubes'
    processes_dir = dir + '/processes'
    chores_dir = dir + '/chores'

    progress = progress_sink
    total_object_count = 0
    effective_max_workers = max(1, int(max_workers if max_workers is not None else _default_max_workers()))

    def _add_object_count(delta: int) -> None:
        nonlocal total_object_count
        if delta > 0:
            total_object_count += int(delta)
    try:
        _processes, _process_errors = deserialize_processes(
            processes_dir,
            progress=progress,
            count_callback=_add_object_count,
        )

        _chores, _chore_errors = deserialize_chores(
            chores_dir,
            progress=progress,
            count_callback=_add_object_count,
        )

        _dimensions, _dim_errors = deserialize_dimensions(
            dimensions_dir,
            progress=progress,
            count_callback=_add_object_count,
            max_workers=effective_max_workers,
        )

        _cubes, _cube_errors = deserialize_cubes(
            cubes_dir,
            _dimensions,
            progress=progress,
            count_callback=_add_object_count,
        )
    finally:
        close_fn = getattr(progress, "close", None)
        if callable(close_fn):
            close_fn()

    _model = Model(cubes=list(_cubes.values()),
                   dimensions=list(_dimensions.values()),
                   processes=list(_processes.values()),
                   chores=list(_chores.values()),
                   total_object_count=total_object_count)
    _errors = _dim_errors | _cube_errors | _process_errors | _chore_errors
    logger.debug(
        "Deserialized model from '%s' (dimensions=%d cubes=%d processes=%d chores=%d errors=%d)",
        dir,
        len(_dimensions),
        len(_cubes),
        len(_processes),
        len(_chores),
        len(_errors),
    )
    return _model, _errors


def deserialize_chores(
    chore_dir,
    *,
    progress: Optional[ProgressSink] = None,
    count_callback: Optional[Callable[[int], None]] = None,
) -> tuple[Dict[str, Chore], Dict[str, str]]:
    chores: Dict[str, Chore] = {}
    chores_errors: Dict[str, str] = {}
    logger.debug("Deserializing chores from '%s'", chore_dir)
    if not os.path.exists(chore_dir):
        return chores, chores_errors

    for file_name in os.listdir(chore_dir):
        file_path = os.path.join(chore_dir, file_name)
        _progress_start(progress, file_path, "reading chore")
        if not file_name.endswith('.json'):
            _progress_mark(progress, file_path)
            continue
        file_name_base, _, _ = file_name.rpartition('.')
        try:
            chore_json = _json_load_file(file_path)
            _progress_mark(progress, file_path)

            tasks = []
            for task_data in chore_json.get('Tasks', []):
                process_bind = task_data.get("Process@odata.bind", "")
                match = re.search(r"Processes\('([^']*)'\)", process_bind)
                if match:
                    tasks.append(Task(process_name=match.group(1), parameters=task_data.get('Parameters', [])))

            chores[chore_json['Name']] = Chore(name=chore_json['Name'], start_time=chore_json['StartTime'],
                                               dst_sensitive=chore_json['DSTSensitive'], active=chore_json['Active'],
                                               execution_mode=chore_json['ExecutionMode'],
                                               frequency=chore_json['Frequency'], tasks=tasks)
            if count_callback is not None:
                count_callback(1)
        except Exception as e:
            chores_link = Chore.uri_for(file_name_base)
            chores_errors[chores_link] = str(e)
            logger.warning("Failed to deserialize chore '%s': %s", file_name, e, exc_info=True)
            _progress_mark(progress, file_path)
    return chores, chores_errors


def deserialize_processes(
    process_dir,
    *,
    progress: Optional[ProgressSink] = None,
    count_callback: Optional[Callable[[int], None]] = None,
) -> tuple[Dict[str, Process], Dict[str, str]]:
    processes: Dict[str, Process] = {}
    process_errors: Dict[str, str] = {}
    logger.debug("Deserializing processes from '%s'", process_dir)

    files = directory_to_dict(process_dir)
    for file_name in list(files.keys()):

        file_name_base, dot, file_name_ext = file_name.rpartition('.')
        process_link = Process.uri_for(file_name_base)

        if file_name_ext != 'json' and file_name_ext != 'ti':
            process_errors[process_link] = 'not a process json or ti file'
            logger.warning("Skipping non-process artifact: '%s'", file_name)
            _progress_mark(progress, os.path.join(process_dir, file_name))
            continue
        if file_name_ext != 'json':
            continue

        files.pop(file_name, None)
        process_json = None
        process_ti = None

        process_file_path = os.path.join(process_dir, file_name)
        _progress_start(progress, process_file_path, "reading process json")
        with open(process_file_path, 'r', encoding='utf-8') as file:
            try:
                data = file.read()
                process_json = _json_load_text(data)
                _progress_mark(progress, process_file_path)
            except Exception as e:
                process_errors[process_link] = e.__repr__()
                logger.warning("Failed to parse process json '%s': %s", file_name, e, exc_info=True)
                _progress_mark(progress, process_file_path)
                continue

        ti_file_name = file_name_base + '.ti'
        if ti_file_name not in files:
            process_errors[process_link] = 'related ti not found at ' + Process.uri_for(file_name_base)
            logger.warning("Missing TI pair for process json '%s'", file_name)
            continue

        ti_file_path = os.path.join(process_dir, ti_file_name)
        _progress_start(progress, ti_file_path, "reading process ti")
        with open(ti_file_path, 'r', encoding='utf-8') as file:
            try:
                data = file.read()
                process_ti = TI.from_string(data)
                _progress_mark(progress, ti_file_path)
            except Exception as e:
                process_errors[process_link] = e.__repr__()
                logger.warning("Failed to parse process TI '%s': %s", ti_file_name, e, exc_info=True)
                _progress_mark(progress, ti_file_path)
            finally:
                files.pop(ti_file_name, None)

        try:
            _process = Process(
                name=process_json['Name'],
                hasSecurityAccess=process_json['HasSecurityAccess'],
                code_link=process_json['Code@Code.link'],
                datasource=None,  # datasource=process_json.get('DataSource'), ?
                parameters=process_json['Parameters'],
                variables=process_json['Variables'],
                ti=process_ti,
            )
            processes[process_json['Name']] = _process
            if count_callback is not None:
                count_callback(1)
        except Exception as e:
            process_errors[process_link] = e.__repr__()
            logger.warning("Failed to build process object for '%s': %s", file_name, e, exc_info=True)

    return processes, process_errors


def _deserialize_single_hierarchy(
    *,
    hierarchy_json_path: str,
    model_root: str,
    dimension_name: str,
    hierarchy_name: str,
    subset_dir_path: str,
    progress: Optional[ProgressSink],
    max_workers: int,
    hash_slot_counter: Optional[count],
) -> Hierarchy:
    elements, edges, subsets = _ensure_hierarchy_store_groups(
        hierarchy_json_path=hierarchy_json_path,
        model_root=model_root,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
        subset_dir_path=subset_dir_path,
        progress=progress,
        max_workers=max_workers,
        hash_slot_counter=hash_slot_counter,
    )
    return Hierarchy(
        name=hierarchy_name,
        elements=elements,
        edges=edges,
        subsets=subsets,
    )


def deserialize_dimensions(
    dimension_dir,
    *,
    progress: Optional[ProgressSink] = None,
    count_callback: Optional[Callable[[int], None]] = None,
    max_workers: Optional[int] = None,
) -> tuple[Dict[str, Dimension], Dict[str, str]]:
    dimensions: Dict[str, Dimension] = {}
    dimension_errors: Dict[str, str] = {}
    logger.debug("Deserializing dimensions from '%s'", dimension_dir)

    files = directory_to_dict(dimension_dir)
    model_root = os.path.dirname(dimension_dir)
    effective_max_workers = max(1, int(max_workers if max_workers is not None else _default_max_workers()))
    hash_slot_counter = count(start=0)

    for file_name in sorted(list(files.keys())):
            file_name_base, dot, file_name_ext = file_name.rpartition('.')
            dim_link = Dimension.uri_for(file_name_base)

            if file_name_ext not in ['json', 'hierarchies']:
                dimension_errors[dim_link] = 'not a dimension json or .hierarchies folder'
                logger.warning("Skipping non-dimension artifact: '%s'", file_name)
                _progress_mark(progress, os.path.join(dimension_dir, file_name))
                continue
            if file_name_ext != 'json':
                continue

            files.pop(file_name, None)
            dim_json = None

            try:
                dim_file_path = os.path.join(dimension_dir, file_name)
                _progress_start(progress, dim_file_path, "reading dimension")
                with open(dim_file_path, 'r', encoding='utf-8') as file:
                    data = file.read()
                    dim_json = _json_load_text(data)
                    _progress_mark(progress, dim_file_path)
            except Exception as e:
                dimension_errors[dim_link] = e.__repr__()
                logger.warning("Failed to parse dimension json '%s': %s", file_name, e, exc_info=True)
                _progress_mark(progress, dim_file_path)
                continue

            try:
                dim_name = dim_json['Name']
                _dimension = Dimension(name=dim_name, hierarchies=[], defaultHierarchy=None)
                dimension_object_count = 1
            except Exception as e:
                dimension_errors[dim_link] = e.__repr__()
                logger.warning("Failed to build dimension object for '%s': %s", file_name, e, exc_info=True)
                continue

            hier_dir_name = file_name_base + '.hierarchies'
            hier_dir_path = os.path.join(dimension_dir, hier_dir_name)

            if hier_dir_name not in files and not os.path.isdir(hier_dir_path):
                dimension_errors[dim_link] = 'no hierarchies found'
                logger.warning("No hierarchy directory found for dimension '%s'", file_name)
                continue

            hiers = files.get(hier_dir_name)
            parsed_hierarchies: dict[str, Hierarchy] = {}
            for hier_file_name in sorted(list(hiers.keys())):
                hierarchy_file_path = os.path.join(hier_dir_path, hier_file_name)
                _progress_start(progress, hierarchy_file_path, "reading hierarchy")
                # Ignore temporary/in-progress hierarchy artifacts.
                if hier_file_name.endswith(".json.inprogress") or hier_file_name.endswith(".tmp.json.meta.json"):
                    _progress_mark(progress, hierarchy_file_path)
                    continue
                hier_file_name_base, dot, file_name_ext = hier_file_name.rpartition('.')
                hier_link = Hierarchy.uri_for(file_name_base, hier_file_name_base)

                if file_name_ext not in ['json', 'subsets']:
                    dimension_errors[hier_link] = 'not a hierarchy json or .subset folder'
                    logger.warning("Skipping non-hierarchy artifact: '%s'", hier_file_name)
                    _progress_mark(progress, hierarchy_file_path)
                    continue
                if file_name_ext != 'json':
                    continue

                hiers.pop(hier_file_name, None)
                subset_dir_name = hier_file_name_base + '.subsets'
                subset_dir_path = os.path.join(hier_dir_path, subset_dir_name)
                try:
                    _hierarchy = _deserialize_single_hierarchy(
                        hierarchy_json_path=hierarchy_file_path,
                        model_root=model_root,
                        dimension_name=file_name_base,
                        hierarchy_name=hier_file_name_base,
                        subset_dir_path=subset_dir_path,
                        progress=progress,
                        max_workers=effective_max_workers,
                        hash_slot_counter=hash_slot_counter,
                    )
                    parsed_hierarchies[hier_file_name_base] = _hierarchy
                    hierarchy_object_count = 1 + len(_hierarchy.subsets) + len(_hierarchy.edges)
                    if _hierarchy.name.strip().lower() != "leaves":
                        hierarchy_object_count += len(_hierarchy.elements)
                    dimension_object_count += hierarchy_object_count
                except Exception as e:
                    hier_link = Hierarchy.uri_for(file_name_base, hier_file_name_base)
                    dimension_errors[hier_link] = str(e)
                    logger.warning(
                        "Failed to parse/build hierarchy '%s' for dimension '%s': %s",
                        hier_file_name,
                        file_name,
                        e,
                        exc_info=True,
                    )

            for hierarchy_name in sorted(parsed_hierarchies.keys()):
                _dimension.hierarchies.append(parsed_hierarchies[hierarchy_name])

            pattern = r"Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)"
            match = re.search(pattern, dim_json['DefaultHierarchy'])
            if match:
                _, default_hierarchy_name = match.groups()
                _dimension.defaultHierarchy = parsed_hierarchies.get(default_hierarchy_name)

            if not _dimension.defaultHierarchy:
                dimension_errors[dim_link] = 'no default hierarchy'
                logger.warning("No default hierarchy resolved for dimension '%s'", file_name)
                continue
            dimensions[_dimension.name] = _dimension
            if count_callback is not None:
                count_callback(dimension_object_count)
    return dimensions, dimension_errors


def deserialize_cubes(
    cubes_dir,
    _dimensions: Dict[str, Dimension],
    *,
    progress: Optional[ProgressSink] = None,
    count_callback: Optional[Callable[[int], None]] = None,
) -> tuple[Dict[str, Cube], Dict[str, str]]:
    cubes: Dict[str, Cube] = {}
    cube_errors: Dict[str, str] = {}
    logger.debug("Deserializing cubes from '%s'", cubes_dir)

    files = directory_to_dict(cubes_dir)
    for file_name in list(files.keys()):
        file_name_base, dot, file_name_ext = file_name.rpartition('.')
        cube_link = Cube.uri_for(file_name_base)

        if file_name_ext not in ['json', 'rules', 'views']:
            cube_errors[cube_link] = 'not a dimension json or .rules or .views folder'
            logger.warning("Skipping non-cube artifact: '%s'", file_name)
            cube_artifact_path = os.path.join(cubes_dir, file_name)
            _progress_start(progress, cube_artifact_path, "skipping non-cube artifact")
            _progress_mark(progress, cube_artifact_path)
            continue
        if file_name_ext != 'json':
            continue

        files.pop(file_name, None)
        cube_json = None

        cube_file_path = os.path.join(cubes_dir, file_name)
        _progress_start(progress, cube_file_path, "reading cube")
        with open(cube_file_path, 'r', encoding='utf-8') as file:
            cube_json = _json_load_text(file.read())
            _progress_mark(progress, cube_file_path)
            rules_list = []
            rule_file_path = os.path.join(cubes_dir, file_name_base + '.rules')
            if os.path.exists(rule_file_path):
                _progress_start(progress, rule_file_path, "reading cube rules")
                with open(rule_file_path, 'r', encoding='utf-8') as file:
                    rule_text = file.read()
                    _progress_mark(progress, rule_file_path)
                    rules_list = _parse_rules(rule_text, cube_name=file_name_base)
            _cube = Cube(name=cube_json['Name'], dimensions=[], rules=rules_list, views=[])

        for dim in cube_json['Dimensions']:
            pattern = r"Dimensions\('([^']*)'\)"
            match = re.search(pattern, dim['@id'])
            if match:
                dimension = match.groups()
                _dimension = _dimensions.get(dimension[0])
                if _dimension:
                    _cube.dimensions.append(_dimension)

        view_dir_name = file_name_base + '.views'
        view_dir_path = os.path.join(cubes_dir, view_dir_name)
        if view_dir_name in files and os.path.isdir(view_dir_path):
            views = files.get(view_dir_name)
            for view_file_name in list(views.keys()):
                view_file_name_base, dot, file_name_ext = view_file_name.rpartition('.')

                view = None
                mdx = None
                if file_name_ext == 'json':
                    _progress_start(progress, os.path.join(view_dir_path, view_file_name), "reading cube view")
                    with open(os.path.join(view_dir_path, view_file_name), 'r', encoding='utf-8') as file:
                        try:
                            data = file.read()
                            view = _json_load_text(data)
                            _progress_mark(progress, os.path.join(view_dir_path, view_file_name))
                        except Exception as e:
                            cube_errors[file_name_base + '.views/' + view_file_name] = e.__repr__()
                            logger.warning(
                                "Failed to parse view '%s' for cube '%s': %s",
                                view_file_name,
                                file_name_base,
                                e,
                                exc_info=True,
                            )
                else:
                    continue

                view_type = (view.get('@type') or '').lower()

                if view_type == 'mdxview':
                    mdx_file_name = view_file_name_base + '.mdx'
                    if mdx_file_name in views:
                        _progress_start(progress, os.path.join(view_dir_path, mdx_file_name), "reading cube view mdx")
                        with open(os.path.join(view_dir_path, mdx_file_name), 'r', encoding='utf-8') as file:
                            try:
                                mdx = file.read()
                                _progress_mark(progress, os.path.join(view_dir_path, mdx_file_name))
                            except Exception as e:
                                cube_errors[file_name_base + '.mdx'] = e.__repr__()
                                logger.warning(
                                    "Failed to parse mdx '%s' for cube '%s': %s",
                                    mdx_file_name,
                                    file_name_base,
                                    e,
                                    exc_info=True,
                                )
                        files.pop(mdx_file_name, None)
                    else:
                        cube_errors[mdx_file_name] = 'mdx not found'
                        continue

                    if not mdx:
                        cube_errors[mdx_file_name] = 'mdx cannot be parsed'
                        continue

                    _cube.views.append(MDXView(name=view['Name'], mdx=mdx))
                elif view_type == 'nativeview':
                    _cube.views.append(
                        NativeView(
                            name=view['Name'],
                            columns=view.get('Columns', []),
                            rows=view.get('Rows', []),
                            titles=view.get('Titles', []),
                            suppress_empty_columns=view.get('SuppressEmptyColumns', False),
                            suppress_empty_rows=view.get('SuppressEmptyRows', False),
                            format_string=view.get('FormatString', '0.#########'),
                        )
                    )
                else:
                    cube_errors[file_name_base + '.views/' + view_file_name] = "unsupported view type"
                    logger.warning(
                        "Unsupported view type for '%s' in cube '%s'",
                        view_file_name,
                        file_name_base,
                    )
        cubes[_cube.name] = _cube
        if count_callback is not None:
            count_callback(1 + len(_cube.rules) + len(_cube.views))
    return cubes, cube_errors


def _parse_rules(rule_text: str, cube_name: str) -> List[Rule]:
    if not rule_text: return []
    rules = []
    seen_names: dict[str, int] = {}

    def _unique_rule_name(area: str) -> str:
        base = Rule.name_from_area(area)
        seen_names[base] = seen_names.get(base, 0) + 1
        if seen_names[base] == 1:
            return base
        return f"{base}_{seen_names[base]}"

    pattern = re.compile(r"(?P<comment>(?:#.*(?:\r\n|\n|$)\s*)*)?(?P<statement>\[.*?\][^;]*;)", re.DOTALL)
    header_match = re.match(r'^(.*?)(?=\[|#|$)', rule_text, re.DOTALL)
    last_pos = 0
    if header_match:
        header_text = header_match.group(1).strip()
        if header_text:
            rules.append(
                Rule(
                    name=_unique_rule_name("[HEADER]"),
                    area="[HEADER]",
                    full_statement=header_text,
                    comment="",
                )
            )
        last_pos = header_match.end()
    for match in pattern.finditer(rule_text, last_pos):
        comment = (match.group('comment') or "").strip()
        statement_text = match.group('statement').strip()
        area_match = re.search(r'(\[.*?\])', statement_text)
        area = area_match.group(1) if area_match else "[UNKNOWN]"
        rules.append(
            Rule(
                name=_unique_rule_name(area),
                area=area,
                full_statement=statement_text,
                comment=comment,
            )
        )
    return rules


def directory_to_dict(path):
    """Converts a directory structure to a nested dictionary."""
    if not os.path.isdir(path):
        logger.debug("Directory '%s' not found, returning empty structure", path)
        return {}
    directory_dict = {}
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        if os.path.isdir(item_path):
            # If the item is a directory, recursively populate its contents
            directory_dict[item] = directory_to_dict(item_path)
        else:
            # If the item is a file, set it to None or any specific value if needed
            directory_dict[item] = None
    return directory_dict
