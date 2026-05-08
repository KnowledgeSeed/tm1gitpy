import logging
import hashlib
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial
from itertools import count
from pathlib import Path
from queue import Empty
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional
from multiprocessing import Manager
import ijson
import orjson
from tm1_git_py.model import Edge
from tm1_git_py.model.chore import Chore
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.element import Element
from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.model.model import Model
from tm1_git_py.model.model_store import (
    DEFAULT_HASH_FETCH_BATCH_SIZE,
    DEFAULT_PARALLEL_HASH_CHUNK_SIZE,
    ModelStore,
    _parallel_identity_chunk_hash_worker,
)
from tm1_git_py.model.store_backed_sequence import StoreBackedSequence
from tm1_git_py.model.process import Process
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.subset import Subset
from tm1_git_py.model.task import Task
from tm1_git_py.model.ti import TI
from tm1_git_py.progress_reporting import (
    NoopProgressSink,
    ProgressKind,
    ProgressEvent,
    ProgressScope,
    ProgressSink,
    ProgressUnit,
)


logger = logging.getLogger(__name__)
DESERIALIZE_PROGRESS_EVERY = 100_000


def _default_max_workers() -> int:
    return max(1, ((os.cpu_count() or 1) // 2) + 1)


def _json_load_text(raw: str) -> Any:
    return orjson.loads(raw)


def _json_load_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as src:
        return _json_load_text(src.read())


def _supports_parallel_identity_hash(normalized: str) -> bool:
    return normalized in ("element", "elements", "edge", "edges", "subset", "subsets")


def _prepare_parallel_hash_jobs(
    *,
    store: ModelStore,
    group_id: int,
    ordered_by_identity: bool = True,
    chunk_size: int = DEFAULT_PARALLEL_HASH_CHUNK_SIZE,
    fetch_batch_size: int = DEFAULT_HASH_FETCH_BATCH_SIZE,
) -> tuple[str, int, list[dict[str, Any]]]:
    if not ordered_by_identity:
        raise ValueError("Parallel content signature requires ordered_by_identity=True.")
    normalized, payload_table, total_rows = store.resolve_parallel_hash_inputs(group_id)
    if not _supports_parallel_identity_hash(normalized):
        raise ValueError(
            f"Unsupported group object type for parallel identity hashing: '{normalized}'"
        )
    chunk_size = max(1, int(chunk_size))
    fetch_batch_size = max(1, int(fetch_batch_size))
    if total_rows == 0:
        return normalized, 0, []
    chunk_offsets = list(range(0, total_rows, chunk_size))
    jobs: list[dict[str, Any]] = []
    for idx, chunk_offset in enumerate(chunk_offsets):
        chunk_limit = max(0, min(chunk_size, total_rows - int(chunk_offset)))
        jobs.append(
            {
                "db_path": store.db_path,
                "payload_table": payload_table,
                "normalized": normalized,
                "group_id": group_id,
                "chunk_idx": idx,
                "chunk_offset": int(chunk_offset),
                "chunk_limit": int(chunk_limit),
                "fetch_batch_size": fetch_batch_size,
            }
        )
    return normalized, total_rows, jobs


def _combine_parallel_chunk_hashes(
    *,
    algo: str,
    empty_hash: str,
    normalized: str,
    total_rows: int,
    chunk_results: list[tuple[int, int, str]],
) -> str:
    if total_rows == 0:
        return empty_hash
    hasher = hashlib.sha256()
    hasher.update((algo + "\n").encode("utf-8"))
    hasher.update((normalized + "\n").encode("utf-8"))
    hasher.update((str(total_rows) + "\n").encode("utf-8"))
    for chunk_idx, chunk_rows, chunk_hash in sorted(chunk_results, key=lambda item: item[0]):
        hasher.update(f"{chunk_idx}:{chunk_rows}:{chunk_hash}\n".encode("utf-8"))
    return hasher.hexdigest()


def _progress_start(
    progress: ProgressSink,
    file_path: str,
    activity: str
) -> None:
    total = 0
    try:
        total = int(os.path.getsize(file_path))
    except OSError:
        total = 0
    progress.on_event(
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.WORKER,
            current=0,
            total=max(1, total),
            unit=ProgressUnit.BYTE,
            message=activity,
            path=file_path
        )
    )


def _progress_mark(
    progress: ProgressSink,
    file_path: str,
    *,
    include_total: bool = True,
) -> None:
    total = 0
    try:
        total = int(os.path.getsize(file_path))
    except OSError:
        total = 0
    progress.on_event(
        ProgressEvent.make(
            kind=ProgressKind.UPDATE,
            scope=ProgressScope.WORKER,
            current=max(1, total),
            total=max(1, total),
            unit=ProgressUnit.BYTE,
            message="completed",
            path=file_path,
            update_total=True,
        )
    )
    if include_total:
        progress.on_event(
            ProgressEvent.make(
                kind=ProgressKind.UPDATE,
                scope=ProgressScope.TOTAL,
                current_delta=max(0, total),
                total=None,
                unit=ProgressUnit.BYTE,
                message="Building internal model",
            )
        )


def _directory_total_bytes(model_dir: str) -> int:
    total_bytes = 0
    for folder_name in ("cubes", "chores", "dimensions", "processes"):
        folder_path = os.path.join(model_dir, folder_name)
        if not os.path.isdir(folder_path):
            continue
        for root, _, files in os.walk(folder_path):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                try:
                    total_bytes += int(os.path.getsize(file_path))
                except OSError:
                    continue
    return total_bytes

def _on_chunk_start(progress_queue: Any, fetch_batch_size: int) -> None:
    progress_queue.put(
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.WORKER,
            current=0,
            total=max(1, int(fetch_batch_size)),
            unit=ProgressUnit.LINE,
            message="hash calculation started"
        )
    )

def _on_chunk_end(progress_queue: Any, row_count: int) -> None:
    progress_queue.put(
        ProgressEvent.make(
            kind=ProgressKind.UPDATE,
            scope=ProgressScope.WORKER,
            current=max(0, int(row_count)),
            total=max(1, int(row_count)),
            unit=ProgressUnit.LINE,
            message="hash calculation part done"
        )
    )


def _drain_chunk_progress_events(progress: ProgressSink, progress_queue: Any) -> None:
    while True:
        try:
            event = progress_queue.get_nowait()
        except Empty:
            break
        progress.on_event(event)

def recalculate_group_content_signature_parallel(
    *,
    progress: Optional[ProgressSink] = None,
    store: ModelStore,
    group_id: int,
    ordered_by_identity: bool = True,
    chunk_size: int = DEFAULT_PARALLEL_HASH_CHUNK_SIZE,
    fetch_batch_size: int = DEFAULT_HASH_FETCH_BATCH_SIZE,
    progress_callback: Optional[Callable[[int], None]] = None,
    progress_event_callback: Optional[Callable[[ProgressEvent], None]] = None,
    process_pool: Optional[ProcessPoolExecutor] = None,
) -> tuple[int, str]:
    normalized, total_rows, jobs = _prepare_parallel_hash_jobs(
        store=store,
        group_id=group_id,
        ordered_by_identity=ordered_by_identity,
        chunk_size=chunk_size,
        fetch_batch_size=fetch_batch_size,
    )
    if total_rows == 0:
        store.commit_group_content_signature(
            group_id,
            row_count=0,
            content_hash=store.EMPTY_CONTENT_HASH,
        )
        return 0, store.EMPTY_CONTENT_HASH

    chunk_results: list[tuple[int, int, str]] = []
    use_parallel = process_pool is not None and len(jobs) > 1
    if not use_parallel and jobs:
        # Keep serial mode deterministic and chunk-size agnostic.
        first_job = dict(jobs[0])
        first_job["chunk_idx"] = 0
        first_job["chunk_offset"] = 0
        first_job["chunk_limit"] = max(0, int(total_rows))
        jobs = [first_job]
    if use_parallel:
        progress_manager = None
        progress_queue = None
        pool_on_chunk_start = None
        pool_on_chunk_end = None
        if progress is not None:
            progress_manager = Manager()
            progress_queue = progress_manager.Queue()
            pool_on_chunk_start = partial(_on_chunk_start, progress_queue)
            pool_on_chunk_end = partial(_on_chunk_end, progress_queue)
        futures = []
        for job in jobs:
            futures.append(
                process_pool.submit(
                    _parallel_identity_chunk_hash_worker,
                    job,
                    on_chunk_start=pool_on_chunk_start,
                    on_chunk_end=pool_on_chunk_end,
                )
            )
        processed_rows = 0
        for future in as_completed(futures):
            chunk_idx, chunk_rows, chunk_hash = future.result()
            chunk_results.append((chunk_idx, chunk_rows, chunk_hash))
            processed_rows += int(chunk_rows)
            if progress is not None and progress_queue is not None:
                _drain_chunk_progress_events(progress, progress_queue)
            if progress_callback is not None:
                progress_callback(processed_rows)
            if progress_event_callback is not None:
                progress_event_callback(
                    ProgressEvent.make(
                        kind=ProgressKind.UPDATE,
                        scope=ProgressScope.WORKER,
                        current=max(0, int(processed_rows)),
                        unit=ProgressUnit.LINE,
                    )
                )
        if progress is not None and progress_queue is not None:
            _drain_chunk_progress_events(progress, progress_queue)
        if progress_manager is not None:
            progress_manager.shutdown()
    else:
        processed_rows = 0
        serial_on_chunk_start = None
        serial_on_chunk_end = None
        if progress is not None:
            def _serial_on_chunk_start(fetch_batch_size_value: int) -> None:
                progress.on_event(
                    ProgressEvent.make(
                        kind=ProgressKind.START,
                        scope=ProgressScope.WORKER,
                        current=0,
                        total=max(1, int(fetch_batch_size_value)),
                        unit=ProgressUnit.LINE,
                        message="hash calculation started",
                    )
                )

            def _serial_on_chunk_end(row_count_value: int) -> None:
                progress.on_event(
                    ProgressEvent.make(
                        kind=ProgressKind.UPDATE,
                        scope=ProgressScope.WORKER,
                        current=max(0, int(row_count_value)),
                        total=max(1, int(row_count_value)),
                        unit=ProgressUnit.LINE,
                        message="hash calculation part done",
                    )
                )

            serial_on_chunk_start = _serial_on_chunk_start
            serial_on_chunk_end = _serial_on_chunk_end
        for job in jobs:
            chunk_idx, chunk_rows, chunk_hash = _parallel_identity_chunk_hash_worker(
                job,
                on_chunk_start=serial_on_chunk_start,
                on_chunk_end=serial_on_chunk_end,
            )
            chunk_results.append((chunk_idx, chunk_rows, chunk_hash))
            processed_rows += int(chunk_rows)
            if progress_callback is not None:
                progress_callback(processed_rows)
            if progress_event_callback is not None:
                progress_event_callback(
                    ProgressEvent.make(
                        kind=ProgressKind.UPDATE,
                        scope=ProgressScope.WORKER,
                        current=max(0, int(processed_rows)),
                        unit=ProgressUnit.LINE,
                    )
                )

    row_count = sum(chunk_row_count for _, chunk_row_count, _ in chunk_results)
    content_hash = _combine_parallel_chunk_hashes(
        algo=store.PARALLEL_HASH_ALGO,
        empty_hash=store.EMPTY_CONTENT_HASH,
        normalized=normalized,
        total_rows=row_count,
        chunk_results=chunk_results,
    )
    store.commit_group_content_signature(
        group_id,
        row_count=row_count,
        content_hash=content_hash,
    )
    return row_count, content_hash


def _recalculate_group_signature_task(
    *,
    model_id: str,
    group_id: int,
    progress: ProgressSink,
    progress_scope: str,
    process_pool: Optional[ProcessPoolExecutor] = None,
) -> tuple[int, str]:
    store = ModelStore.for_model_id(model_id)
    total_lines = store.row_count(group_id)
    progress.on_event(
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.WORKER,
            current=0,
            total=max(1, total_lines),
            unit=ProgressUnit.LINE,
            message="recalculating hash",
            path=progress_scope,
        )
    )
    def _on_progress(processed_rows: int) -> None:
        progress.on_event(
            ProgressEvent.make(
                kind=ProgressKind.UPDATE,
                scope=ProgressScope.WORKER,
                current=max(0, int(processed_rows)),
                total=max(1, total_lines),
                unit=ProgressUnit.LINE,
                message="recalculating hash",
                path=progress_scope,
            )
        )

    row_count, content_hash = recalculate_group_content_signature_parallel(
        progress=progress,
        store=store,
        group_id=group_id,
        ordered_by_identity=True,
        fetch_batch_size=5_000,
        progress_callback=_on_progress,
        process_pool=process_pool,

    )
    progress.on_event(
        ProgressEvent.make(
            kind=ProgressKind.UPDATE,
            scope=ProgressScope.WORKER,
            current=max(0, int(row_count)),
            total=max(1, total_lines),
            unit=ProgressUnit.LINE,
            message="recalculating hash",
            path=progress_scope,
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
    progress: ProgressSink,
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

    last_total_position = int(start_fraction * file_size) if file_size > 0 else 0

    with open(hierarchy_json_path, "rb") as src:
        for index, payload in enumerate(ijson.items(src, item_prefix), start=1):
            if not isinstance(payload, dict):
                raise ValueError(f"Malformed hierarchy json: non-object payload in array '{key}'")
            emitted = True
            if index % 1_000 == 0:
                position = int(src.tell())
                if position > reported_at:
                    scaled_position = max(0, int(_scaled_position(position)))
                    progress.on_event(
                        ProgressEvent.make(
                            kind=ProgressKind.UPDATE,
                            scope=ProgressScope.WORKER,
                            current=scaled_position,
                            total=max(1, file_size),
                            unit=ProgressUnit.BYTE,
                            message="reading hierarchy",
                            path=hierarchy_json_path,
                        )
                    )
                    delta = max(0, scaled_position - last_total_position)
                    if delta > 0:
                        progress.on_event(
                            ProgressEvent.make(
                                kind=ProgressKind.UPDATE,
                                scope=ProgressScope.TOTAL,
                                current_delta=delta,
                                total=None,
                                unit=ProgressUnit.BYTE,
                                message="Building internal model",
                            )
                        )
                        last_total_position = scaled_position
                    reported_at = position
            yield payload
        final_position = max(0, int(_scaled_position(int(src.tell()))))
        progress.on_event(
            ProgressEvent.make(
                kind=ProgressKind.UPDATE,
                scope=ProgressScope.WORKER,
                current=final_position,
                total=max(1, file_size),
                unit=ProgressUnit.BYTE,
                message="reading hierarchy",
                path=hierarchy_json_path,
            )
        )
        final_delta = max(0, final_position - last_total_position)
        if final_delta > 0:
            progress.on_event(
                ProgressEvent.make(
                    kind=ProgressKind.UPDATE,
                    scope=ProgressScope.TOTAL,
                    current_delta=final_delta,
                    total=None,
                    unit=ProgressUnit.BYTE,
                    message="Building internal model",
                )
            )
    if not emitted and not _hierarchy_has_top_level_key(hierarchy_json_path, key):
        if key == "Edges":
            return
        raise ValueError(f"Malformed hierarchy json: key '{key}' not found")


def _hierarchy_array_has_items(hierarchy_json_path: str, key: str) -> bool:
    item_prefix = f"{key}.item"
    with open(hierarchy_json_path, "rb") as src:
        for payload in ijson.items(src, item_prefix):
            if isinstance(payload, dict):
                return True
    return False


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
    model_id: str,
    dimension_name: str,
    hierarchy_name: str,
    subset_dir_path: str,
    progress: ProgressSink,
    max_workers: int = 1,
    process_pool: Optional[ProcessPoolExecutor] = None,
    hash_slot_counter: Optional[count] = None,
) -> tuple[StoreBackedSequence[Element], StoreBackedSequence[Edge], StoreBackedSequence[Subset]]:
    store = ModelStore.for_model_id(model_id)
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
    if not needs_elements_rebuild:
        source_has_elements = _hierarchy_array_has_items(hierarchy_json_path, "Elements")
        cached_has_elements = len(elements) > 0
        if source_has_elements != cached_has_elements:
            logger.info(
                "Rebuilding stale cached elements for %s/%s: source_has_elements=%s cached_has_elements=%s",
                dimension_name,
                hierarchy_name,
                source_has_elements,
                cached_has_elements,
            )
            needs_elements_rebuild = True
    if not needs_edges_rebuild:
        source_has_edges = _hierarchy_array_has_items(hierarchy_json_path, "Edges")
        cached_has_edges = len(edges) > 0
        if source_has_edges != cached_has_edges:
            logger.info(
                "Rebuilding stale cached edges for %s/%s: source_has_edges=%s cached_has_edges=%s",
                dimension_name,
                hierarchy_name,
                source_has_edges,
                cached_has_edges,
            )
            needs_edges_rebuild = True
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
        _recalculate_group_signature_task(
            model_id=model_id,
            group_id=group_id,
            progress=progress,
            progress_scope=scope,
            process_pool=process_pool,
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
    elif os.path.isdir(subset_dir_path):
        for subset_file_name in sorted(os.listdir(subset_dir_path)):
            _progress_mark(progress, os.path.join(subset_dir_path, subset_file_name))
    if needs_elements_rebuild or needs_edges_rebuild:
        _progress_mark(progress, hierarchy_json_path, include_total=False)
    else:
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
    model_id: Optional[str] = None,
    *,
    progress_sink: Optional[ProgressSink] = None,
    max_workers: Optional[int] = None,
) -> tuple[Model, dict[str, str]]:
    logger.debug("Deserializing model from '%s'", dir)
    dir = _handle_long_path(dir)
    resolved_model_id = (model_id or Path(dir).resolve().name).strip()
    if not resolved_model_id:
        raise ValueError("model_id must not be empty")

    dimensions_dir = dir + '/dimensions'
    cubes_dir = dir + '/cubes'
    processes_dir = dir + '/processes'
    chores_dir = dir + '/chores'

    progress = progress_sink if progress_sink is not None else NoopProgressSink()
    progress.on_event(
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.TOTAL,
            current=0,
            total=max(1, _directory_total_bytes(dir)),
            unit=ProgressUnit.BYTE,
            message="Building internal model",
        )
    )
    total_object_count = 0
    effective_max_workers = max(1, int(max_workers if max_workers is not None else _default_max_workers()))
    hash_pool: Optional[ProcessPoolExecutor] = None
    if effective_max_workers > 1:
        try:
            hash_pool = ProcessPoolExecutor(max_workers=effective_max_workers - 1)
        except (OSError, PermissionError, NotImplementedError):
            logger.warning(
                "Falling back to single-process hashing; failed to initialize ProcessPoolExecutor",
                exc_info=True,
            )

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
            resolved_model_id,
            progress=progress,
            count_callback=_add_object_count,
            max_workers=effective_max_workers,
            process_pool=hash_pool,
        )

        _cubes, _cube_errors = deserialize_cubes(
            cubes_dir,
            _dimensions,
            progress=progress,
            count_callback=_add_object_count,
        )
    finally:
        if hash_pool is not None:
            hash_pool.shutdown(wait=True)
        progress.close()

    _model = Model(cubes=list(_cubes.values()),
                   dimensions=list(_dimensions.values()),
                   processes=list(_processes.values()),
                   chores=list(_chores.values()),
                   model_id=resolved_model_id,
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
    progress = progress if progress is not None else NoopProgressSink()
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
    progress = progress if progress is not None else NoopProgressSink()
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
    model_id: str,
    dimension_name: str,
    hierarchy_name: str,
    subset_dir_path: str,
    progress: ProgressSink,
    max_workers: int,
    process_pool: Optional[ProcessPoolExecutor],
    hash_slot_counter: Optional[count],
) -> Hierarchy:
    elements, edges, subsets = _ensure_hierarchy_store_groups(
        hierarchy_json_path=hierarchy_json_path,
        model_id=model_id,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
        subset_dir_path=subset_dir_path,
        progress=progress,
        max_workers=max_workers,
        process_pool=process_pool,
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
    model_id: str,
    *,
    progress: Optional[ProgressSink] = None,
    count_callback: Optional[Callable[[int], None]] = None,
    max_workers: Optional[int] = None,
    process_pool: Optional[ProcessPoolExecutor] = None,
) -> tuple[Dict[str, Dimension], Dict[str, str]]:
    progress = progress if progress is not None else NoopProgressSink()
    model_id = model_id.strip()
    if not model_id:
        raise ValueError("model_id must not be empty")
    dimensions: Dict[str, Dimension] = {}
    dimension_errors: Dict[str, str] = {}
    logger.debug("Deserializing dimensions from '%s'", dimension_dir)

    files = directory_to_dict(dimension_dir)
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
                    model_id=model_id,
                    dimension_name=file_name_base,
                    hierarchy_name=hier_file_name_base,
                    subset_dir_path=subset_dir_path,
                    progress=progress,
                    max_workers=effective_max_workers,
                    process_pool=process_pool,
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
        default_hierarchy_payload = dim_json.get("DefaultHierarchy")
        default_hierarchy_ref = default_hierarchy_payload
        if isinstance(default_hierarchy_payload, dict):
            default_hierarchy_ref = (
                default_hierarchy_payload.get("@id")
                or default_hierarchy_payload.get("id")
            )
        match = re.search(pattern, default_hierarchy_ref or "")
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
    progress = progress if progress is not None else NoopProgressSink()
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
                    #rules_list = _parse_rules(rule_text, cube_name=file_name_base)
                    rules_list = []
                    if rule_text:
                        rules_list = [Rule(area="[default]", full_statement=rule_text, comment="", name="default")]
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

                    meta_raw = view.get("Meta")
                    meta = dict(meta_raw) if isinstance(meta_raw, dict) else None
                    _cube.views.append(
                        MDXView(
                            name=view["Name"],
                            mdx=mdx,
                            format_string=view.get("FormatString", "0.#########"),
                            meta=meta,
                        )
                    )
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
