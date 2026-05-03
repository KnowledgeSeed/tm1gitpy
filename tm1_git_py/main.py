import argparse
import logging
import queue
import shutil
import sys
import threading
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import Manager
from pathlib import Path
import tracemalloc
from typing import Any

from TM1py import TM1Service

from tm1_git_py import Changeset
from tm1_git_py.changeset import import_changeset
from tm1_git_py.comparator import Comparator, TqdmComparatorProgressSink
from tm1_git_py.config import TM1ServersConfig
from tm1_git_py.deserializer import deserialize_model
from tm1_git_py.exporter import export
from tm1_git_py.filter import filter, import_filter
from tm1_git_py.logging_config import setup_logging
from tm1_git_py.model import Model
from tm1_git_py.model.chore import Chore
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.model_store import ModelStore
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.model.process import Process
from tm1_git_py.model.rule import Rule
from tm1_git_py.process_pool import ignore_sigint_in_worker, shutdown_process_pool_now
from tm1_git_py.progress_reporting import (
    CallbackProgressSink,
    CompositeProgressSink,
    LoggingProgressSink,
    ProgressEvent,
    ProgressSink,
    TqdmProgressSink,
)
from tm1_git_py.serializer import serialize_model
from tm1_git_py.worker_config import resolve_worker_counts

logger = logging.getLogger(__name__)


def _normalize_max_workers(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return None


def _split_compare_workers(max_workers: int) -> tuple[int, int]:
    total = max(1, int(max_workers))
    source_workers = max(1, total // 2)
    target_workers = max(1, total - source_workers)
    return source_workers, target_workers


def _rebind_model_store_handles(model: Model, model_root: Path) -> Model:
    """Rebind hierarchy store-backed sequences to a connection in the current thread."""
    model_id = (getattr(model, "model_id", None) or model_root.name).strip()
    if not model_id:
        model_id = "default"
    ModelStore.for_model_id(model_id)
    rebuilt_dimensions: list[Dimension] = []
    dimensions_by_name: dict[str, Dimension] = {}

    for dim in model.dimensions:
        rebuilt_hierarchies: list[Hierarchy] = []
        for hierarchy in dim.hierarchies:
            rebuilt_hierarchies.append(
                Hierarchy(
                    name=hierarchy.name,
                    dimension_name=dim.name,
                    model_id=model_id,
                    reuse_existing_store=True,
                )
            )
        default_hierarchy = None
        existing_default = getattr(dim, "defaultHierarchy", None)
        if existing_default is not None:
            default_hierarchy = next(
                (h for h in rebuilt_hierarchies if h.name == existing_default.name),
                None,
            )
        if default_hierarchy is None and rebuilt_hierarchies:
            default_hierarchy = rebuilt_hierarchies[0]
        rebuilt_dimension = Dimension(
            name=dim.name,
            hierarchies=rebuilt_hierarchies,
            defaultHierarchy=default_hierarchy,
        )
        rebuilt_dimensions.append(rebuilt_dimension)
        dimensions_by_name[rebuilt_dimension.name] = rebuilt_dimension

    model.dimensions = rebuilt_dimensions
    for cube in model.cubes:
        cube.dimensions = [dimensions_by_name.get(dim.name, dim) for dim in cube.dimensions]
    return model


def _tm1_connection(server_name: str) -> TM1Service:
    config = TM1ServersConfig()
    config.load()
    return _tm1_connection_from_config(config, server_name)


def _tm1_connection_from_config(config: TM1ServersConfig, server_name: str) -> TM1Service:
    server_config = config.get(server_name)
    logger.debug(
        "Creating TM1 connection for server='%s' base_url='%s' user='%s'",
        server_name,
        server_config.base_url,
        server_config.user,
    )

    tm1 = TM1Service(
        base_url=server_config.base_url,
        user=server_config.user,
        password=server_config.password or ""
    )
    return tm1


def _model_to_compare_snapshot(model: Model) -> dict:
    dimensions_payload = []
    for dim in model.dimensions:
        default_name = getattr(getattr(dim, "defaultHierarchy", None), "name", None)
        dimensions_payload.append(
            {
                "name": dim.name,
                "default_hierarchy_name": default_name,
                "hierarchy_names": [hier.name for hier in dim.hierarchies],
            }
        )

    cubes_payload = []
    for cube in model.cubes:
        rules_payload = [
            rule.to_dict()
            for rule in cube.rules
        ]
        views_payload = []
        for view in cube.views:
            view_payload = dict(view.to_dict())
            view_payload["_view_type"] = view.__class__.__name__
            views_payload.append(view_payload)
        cubes_payload.append(
            {
                "name": cube.name,
                "dimension_names": [dim.name for dim in cube.dimensions],
                "rules": rules_payload,
                "views": views_payload,
            }
        )

    return {
        "model_id": getattr(model, "model_id", None),
        "dimensions": dimensions_payload,
        "cubes": cubes_payload,
        "processes": [process.to_dict() for process in model.processes],
        "chores": [chore.to_dict() for chore in model.chores],
        "total_object_count": getattr(model, "total_object_count", None),
    }


def _model_from_compare_snapshot(snapshot: dict) -> Model:
    dimensions: list[Dimension] = []
    dimensions_by_name: dict[str, Dimension] = {}
    for dim_payload in snapshot.get("dimensions", []):
        dimension_name = str(dim_payload.get("name", ""))
        hierarchy_names = [str(item) for item in dim_payload.get("hierarchy_names", [])]
        hierarchies = [
            Hierarchy(
                name=hierarchy_name,
                elements=[],
                edges=[],
                subsets=[],
            )
            for hierarchy_name in hierarchy_names
        ]
        default_name = dim_payload.get("default_hierarchy_name")
        default_hierarchy = next((hier for hier in hierarchies if hier.name == default_name), None)
        if default_hierarchy is None and hierarchies:
            default_hierarchy = hierarchies[0]
        if default_hierarchy is None:
            default_hierarchy = Hierarchy(name=dimension_name, elements=[], edges=[], subsets=[])
            hierarchies = [default_hierarchy]
        dimension_obj = Dimension(
            name=dimension_name,
            hierarchies=hierarchies,
            defaultHierarchy=default_hierarchy,
        )
        dimensions.append(dimension_obj)
        dimensions_by_name[dimension_name] = dimension_obj

    cubes: list[Cube] = []
    for cube_payload in snapshot.get("cubes", []):
        cube_name = str(cube_payload.get("name", ""))
        cube_dimension_names = [str(item) for item in cube_payload.get("dimension_names", [])]
        cube_dimensions = []
        for dim_name in cube_dimension_names:
            dim_obj = dimensions_by_name.get(dim_name)
            if dim_obj is None:
                fallback_hier = Hierarchy(name=dim_name, elements=[], edges=[], subsets=[])
                dim_obj = Dimension(name=dim_name, hierarchies=[fallback_hier], defaultHierarchy=fallback_hier)
                dimensions_by_name[dim_name] = dim_obj
                dimensions.append(dim_obj)
            cube_dimensions.append(dim_obj)
        cube_rules = [
            Rule.from_dict(payload, source_path=f"cubes/{cube_name}.rules", cube_name=cube_name)
            for payload in cube_payload.get("rules", [])
        ]
        cube_views = []
        for payload in cube_payload.get("views", []):
            view_payload = dict(payload)
            view_type = str(view_payload.pop("_view_type", "MDXView"))
            if view_type == "NativeView":
                cube_views.append(NativeView.from_dict(view_payload))
            else:
                cube_views.append(MDXView.from_dict(view_payload))
        cubes.append(Cube(name=cube_name, dimensions=cube_dimensions, rules=cube_rules, views=cube_views))

    processes = [Process.from_dict(payload) for payload in snapshot.get("processes", [])]
    chores = [Chore.from_dict(payload) for payload in snapshot.get("chores", [])]
    return Model(
        cubes=cubes,
        dimensions=dimensions,
        processes=processes,
        chores=chores,
        model_id=str(snapshot.get("model_id") or "default"),
        total_object_count=snapshot.get("total_object_count"),
    )


def _deserialize_model_worker(
    model_dir: str,
    tqdm_group_index: int,
    progress_queue: Any,
    max_workers: int,
) -> tuple[dict, dict[str, str]]:
    def _emit_progress(event: ProgressEvent) -> None:
        progress_queue.put((tqdm_group_index, event))

    model, errors = deserialize_model(
        model_dir,
        progress_sink=CallbackProgressSink(_emit_progress),
        max_workers=max(1, int(max_workers)),
    )
    return _model_to_compare_snapshot(model), errors


def _consume_compare_progress_events(
    progress_queue: Any,
    source_sink: ProgressSink,
    target_sink: ProgressSink,
    stop_event: threading.Event,
) -> None:
    while True:
        try:
            if stop_event.is_set():
                try:
                    item = progress_queue.get_nowait()
                except queue.Empty:
                    break
            else:
                try:
                    item = progress_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
        except (BrokenPipeError, EOFError, OSError):
            # Manager queue can disappear during shutdown; exit silently.
            break
        if item is None:
            if stop_event.is_set():
                break
            continue
        tqdm_group_index, event = item
        if int(tqdm_group_index) == 0:
            source_sink.on_event(event)
        else:
            target_sink.on_event(event)


def _prepare_model_folder(model_folder: str, overwrite: bool = False):
    model_path = Path(model_folder)
    if model_path.exists() and model_path.is_dir():
        if not overwrite:
            logger.error(
                "Model folder '%s' already exists. Use --overwrite flag to clear and overwrite.",
                model_folder,
            )
            sys.exit(1)
        logger.info("Clearing existing model folder: %s", model_folder)
        shutil.rmtree(model_folder)


def _load_filter_rules(filter_file: str | None) -> list[str]:
    if not filter_file:
        return []
    raw = str(filter_file).strip()

    def _load_from_file(path_str: str) -> list[str]:
        filter_path = Path(path_str).expanduser().resolve()
        if not filter_path.exists():
            logger.error("Filter file '%s' not found.", path_str)
            sys.exit(1)
        try:
            filter_rules = import_filter(str(filter_path))
            logger.info("Loaded %d filter rule(s) from: %s", len(filter_rules), filter_path)
            return filter_rules
        except Exception:
            logger.exception("Error loading filter from: %s", filter_path)
            sys.exit(1)

    # Explicit file URI form: file://examples/filter.txt
    if raw.startswith("file://"):
        file_uri_path = raw[len("file://"):].strip()
        return _load_from_file(file_uri_path)

    # Inline comma-separated rules form.
    if "," in raw:
        rules = [part.strip() for part in raw.split(",") if part.strip()]
        logger.info("Loaded %d inline filter rule(s)", len(rules))
        return rules

    # Default existing behavior: treat as file path.
    return _load_from_file(raw)


def _filter(model, filter_rules: list[str]) -> Model:
    if filter_rules:
        logger.info("Applying %d filter rule(s)", len(filter_rules))
        try:
            filtered_model = filter(model, filter_rules)
            logger.info("Filter applied successfully")
            return filtered_model
        except Exception:
            logger.exception("Error applying filter rules")
            sys.exit(1)

    logger.debug("No filter rules provided, skipping filtering")
    return model


def _add_common_cli_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Optional log file path or directory for timestamped execution logs",
    )
    p.add_argument(
        "--console-logs",
        action="store_true",
        help="Enable console log output in addition to progress UI",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable detailed worker/thread progress bars in the terminal progress UI",
    )


def _cmd_export(args: argparse.Namespace) -> None:
    config = TM1ServersConfig()
    config.load()
    tm1_service = _tm1_connection_from_config(config, args.server)
    model_output_folder = args.model_output_folder or "export"
    model_output_path = Path(model_output_folder).expanduser().resolve()

    _prepare_model_folder(model_output_folder, args.overwrite)

    filter_rules = _load_filter_rules(args.filter)

    logger.info("Exporting model to folder: %s", model_output_folder)
    model_id = model_output_path.name.strip()
    if not model_id:
        raise ValueError("model_id must not be empty")
    ModelStore.for_model_id(model_id)
    requested_max_workers = _normalize_max_workers(args.max_workers)
    worker_counts = resolve_worker_counts(requested_max_workers)

    # sinkks
    debug_progress = bool(getattr(args, "debug", False))
    tqdm_export_sink = TqdmProgressSink(worker_count=worker_counts.cpu_workers + worker_counts.io_workers, base_position=0, leave=True, thread_tracing_enabled=debug_progress)
    export_sink: list[ProgressSink] = [tqdm_export_sink]
    if bool(args.log_file):
        export_sink.append(LoggingProgressSink(logger))
    export_progress_sink: ProgressSink = (
        export_sink[0] if len(export_sink) == 1 else CompositeProgressSink(export_sink)
    )
    exported_model, export_errors = export(tm1_service, model_id=model_id, filter_rules_list=filter_rules, progress_sink=export_progress_sink, max_workers=requested_max_workers)
    tqdm_export_sink.close()

    if export_errors and any(export_errors.values()):
        logger.warning("Export errors encountered")
        for error_type, errors in export_errors.items():
            if errors:
                logger.warning("Export error category=%s details=%s", error_type, errors)
    else:
        logger.info("Export completed successfully with no errors")

    tqdm_serialize_sink = TqdmProgressSink(worker_count=worker_counts.cpu_workers + worker_counts.io_workers, base_position=0, leave=True, thread_tracing_enabled=debug_progress)
    serialize_sinks: list[ProgressSink] = [tqdm_serialize_sink]
    if bool(args.log_file):
        serialize_sinks.append(LoggingProgressSink(logger))
    serialize_progress_sink: ProgressSink = (
        serialize_sinks[0] if len(serialize_sinks) == 1 else CompositeProgressSink(serialize_sinks)
    )
    serialize_model(exported_model, model_output_folder, progress_sink=serialize_progress_sink, max_workers=requested_max_workers)
    tqdm_serialize_sink.close()
    logger.info("Model serialized to: %s", model_output_folder)

def _cmd_filter(args: argparse.Namespace) -> None:
    model_folder = args.model_folder or "export"
    model_output_folder = args.model_output_folder or "export"
    logger.info("Loading model from folder: %s", model_folder)

    _prepare_model_folder(model_output_folder, args.overwrite)
    filter_sinks: list[ProgressSink] = [
        TqdmProgressSink(
            base_position=0,
            worker_count=resolve_worker_counts(None).cpu_workers,
            leave=False,
            thread_tracing_enabled=bool(getattr(args, "debug", False)),
        )
    ]
    if bool(args.log_file):
        filter_sinks.append(LoggingProgressSink(logger))
    filter_progress_sink: ProgressSink = (
        filter_sinks[0] if len(filter_sinks) == 1 else CompositeProgressSink(filter_sinks)
    )
    model, errors = deserialize_model(
        model_folder,
        progress_sink=filter_progress_sink,
        max_workers=None,
    )
    if errors:
        logger.warning("Deserialization completed with %d error(s)", len(errors))

    filter_rules = _load_filter_rules(args.filter_rules)
    filtered_model = _filter(model, filter_rules)

    serialize_model(filtered_model, model_output_folder)
    logger.info("Model serialized to: %s", model_output_folder)


def _cmd_compare(args: argparse.Namespace) -> None:
    source = Path(args.source).expanduser().resolve()
    target = Path(args.target).expanduser().resolve()
    if not source.is_dir():
        logger.error("Source model path is not a directory: %s", source)
        sys.exit(1)
    if not target.is_dir():
        logger.error("Target model path is not a directory: %s", target)
        sys.exit(1)

    ModelStore.for_model_id(source.name)
    ModelStore.for_model_id(target.name)

    requested_max_workers = _normalize_max_workers(args.max_workers)
    worker_counts = resolve_worker_counts(requested_max_workers)
    source_workers, target_workers = _split_compare_workers(worker_counts.cpu_workers)

    source_tqdm = TqdmProgressSink(
        # str(source),
        worker_count=source_workers,
        base_position=0,
        leave=False,
        thread_tracing_enabled=bool(getattr(args, "debug", False)),
    )
    target_tqdm = TqdmProgressSink(
        # str(target),
        worker_count=target_workers,
        base_position=source_workers+1,
        leave=False,
        thread_tracing_enabled=bool(getattr(args, "debug", False)),
    )
    source_progress_sinks: list[ProgressSink] = [source_tqdm]
    target_progress_sinks: list[ProgressSink] = [target_tqdm]
    if bool(args.log_file):
        source_progress_sinks.append(LoggingProgressSink(logger))
        target_progress_sinks.append(LoggingProgressSink(logger))
    source_progress_sink: ProgressSink = (
        source_progress_sinks[0] if len(source_progress_sinks) == 1 else CompositeProgressSink(source_progress_sinks)
    )
    target_progress_sink: ProgressSink = (
        target_progress_sinks[0] if len(target_progress_sinks) == 1 else CompositeProgressSink(target_progress_sinks)
    )
    manager = Manager()
    progress_queue = manager.Queue()
    stop_event = threading.Event()
    progress_thread = threading.Thread(
        target=_consume_compare_progress_events,
        args=(progress_queue, source_progress_sink, target_progress_sink, stop_event),
        daemon=True,
    )
    progress_thread.start()
    logger.info("Loading source model from %s", source)
    pool: ProcessPoolExecutor | None = None
    try:
        pool = ProcessPoolExecutor(max_workers=2, initializer=ignore_sigint_in_worker)
        source_future = pool.submit(_deserialize_model_worker, str(source), 0, progress_queue, source_workers)
        target_future = pool.submit(_deserialize_model_worker, str(target), 1, progress_queue, target_workers)
        source_snapshot, err_source = source_future.result()
        target_snapshot, err_target = target_future.result()
    except KeyboardInterrupt:
        if pool is not None:
            shutdown_process_pool_now(pool)
            pool = None
        raise
    finally:
        if pool is not None:
            pool.shutdown()
        stop_event.set()
        progress_thread.join()
        source_progress_sink.close()
        target_progress_sink.close()
        manager.shutdown()
    model_source = _model_from_compare_snapshot(source_snapshot)
    model_target = _model_from_compare_snapshot(target_snapshot)
    model_source = _rebind_model_store_handles(model_source, source)
    model_target = _rebind_model_store_handles(model_target, target)

    if err_source:
        logger.warning("Source deserialization reported %d error(s)", len(err_source))
    logger.info("Loading target model from %s", target)
    if err_target:
        logger.warning("Target deserialization reported %d error(s)", len(err_target))

    extra_filter = _load_filter_rules(args.filter_rules) if args.filter_rules else None

    comparator = Comparator()
    compare_tqdm = TqdmComparatorProgressSink(
        enable_fallback_logs=bool(args.log_file),
        preferred_slot_index=0,
    )
    compare_sinks: list[ProgressSink] = [compare_tqdm]
    if bool(args.log_file):
        compare_sinks.append(LoggingProgressSink(logger))
    compare_progress_sink: ProgressSink = (
        compare_sinks[0] if len(compare_sinks) == 1 else CompositeProgressSink(compare_sinks)
    )
    try:
        changeset = comparator.compare(
            model_source,
            model_target,
            mode=args.mode,
            filter_rules=extra_filter,
            progress_sink=compare_progress_sink,
        )
    finally:
        compare_progress_sink.close()

    out = args.output
    if not out:
        out = "changeset.yaml" if args.format == "yaml" else "changeset.json"
    output_path = Path(out).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    changeset.export(output_path)
    if args.format == "json":
        logger.info("Wrote JSON changeset (%d change(s)) to %s", len(changeset.changes), output_path)
    else:
        logger.info("Wrote YAML changeset (%d change(s)) to %s", len(changeset.changes), output_path)


def _cmd_apply(args: argparse.Namespace) -> None:
    changeset_path = Path(args.changeset).expanduser().resolve()
    if not changeset_path.is_file():
        logger.error("Changeset file not found: %s", changeset_path)
        sys.exit(1)

    tm1_service = _tm1_connection(args.server)
    changeset = import_changeset(changeset_path)

    status_dir = Path(args.status_dir).expanduser().resolve() if args.status_dir else None
    apply_sinks: list[ProgressSink] = [
        TqdmProgressSink(
            base_position=0,
            worker_count=1,
            leave=False,
            thread_tracing_enabled=bool(getattr(args, "debug", False)),
        )
    ]
    if bool(args.log_file):
        apply_sinks.append(LoggingProgressSink(logger))
    apply_progress_sink: ProgressSink = (
        apply_sinks[0] if len(apply_sinks) == 1 else CompositeProgressSink(apply_sinks)
    )
    try:
        ok, errors = changeset.apply(
            tm1_service,
            status_dir=status_dir,
            execution_id=args.execution_id,
            fail_fast=not args.no_fail_fast,
            progress_sink=apply_progress_sink,
        )
    finally:
        apply_progress_sink.close()
    if ok:
        logger.info("Apply finished successfully")
    else:
        logger.error("Apply finished with failures: %s", errors)
        sys.exit(1)


def _cmd_changeset_filter(args: argparse.Namespace) -> None:
    changeset_path = Path(args.changeset_path).expanduser().resolve()
    if not changeset_path.is_file():
        logger.error("Changeset file not found: %s", changeset_path)
        sys.exit(1)

    filter_rules = _load_filter_rules(args.filter_rules)
    changeset = import_changeset(changeset_path)
    toggled_count = changeset.filter(filter_rules)
    changeset.export(changeset_path)
    logger.info(
        "Applied changeset filter rules and toggled apply for %d change(s): %s",
        toggled_count,
        changeset_path,
    )


def main():
    tracemalloc.start()
    parser = argparse.ArgumentParser(description="TM1 Git Py - TM1 model export, filter, compare, and apply")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="Export model from TM1 to a folder")
    _add_common_cli_options(p_export)
    p_export.add_argument("-s", "--server", type=str, required=True, help="TM1 server name from tm1servers config")
    p_export.add_argument(
        "-mo", "--model-output-folder",
        type=str,
        default="export",
        help="Folder to write the serialized model",
    )
    p_export.add_argument("-o", "--overwrite", action="store_true", help="Clear output folder if it already exists")
    p_export.add_argument("-f", "--filter", type=str, help="Path to filter rules file for export")
    p_export.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help=(
            "CPU worker count. If defined, IO workers are 2x this value. "
            "If omitted, CPU workers default to cpu_count/2 + 1 and IO workers to cpu_count*2."
        ),
    )
    p_export.set_defaults(handler=_cmd_export)

    p_filter = sub.add_parser(
        "model-filter",
        aliases=["filter"],
        help="Load a model folder, apply filter rules, write output folder",
    )
    _add_common_cli_options(p_filter)
    p_filter.add_argument("-m", "--model-folder", type=str, default="export", help="Input model folder")
    p_filter.add_argument(
        "-mo", "--model-output-folder",
        type=str,
        default="export",
        help="Output folder for filtered model",
    )
    p_filter.add_argument("-o", "--overwrite", action="store_true", help="Clear output folder if it already exists")
    p_filter.add_argument(
        "-f",
        "--filter-rules",
        type=str,
        help="Filter rules as file path, file:// URI, or comma-separated rules",
    )
    p_filter.set_defaults(handler=_cmd_filter)

    p_compare = sub.add_parser("compare", help="Compare two model folders and write a changeset file")
    _add_common_cli_options(p_compare)
    p_compare.add_argument(
        "--source",
        type=str,
        required=True,
        help="Base / old model directory (e.g. Git branch A)",
    )
    p_compare.add_argument(
        "--target",
        type=str,
        required=True,
        help="New model directory (e.g. Git branch B)",
    )
    p_compare.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Output changeset path (default: changeset.yaml or changeset.json by --format)",
    )
    p_compare.add_argument(
        "--mode",
        type=str,
        choices=["full", "add_only"],
        default="full",
        help="full: add/remove/modify; add_only: add/modify only",
    )
    p_compare.add_argument(
        "-f",
        "--filter-rules",
        type=str,
        help="Optional filter rules as file path, file:// URI, or comma-separated rules",
    )
    p_compare.add_argument(
        "--format",
        type=str,
        choices=["yaml", "json"],
        default="yaml",
        help="Changeset output format",
    )
    p_compare.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help=(
            "CPU worker count for compare deserialization. If omitted, defaults to cpu_count/2 + 1. "
            "Workers are split between source and target; odd values assign one extra to target."
        ),
    )
    p_compare.set_defaults(handler=_cmd_compare)

    p_apply = sub.add_parser("apply", help="Apply a changeset file to a TM1 server")
    _add_common_cli_options(p_apply)
    p_apply.add_argument("-s", "--server", type=str, required=True, help="TM1 server name from tm1servers config")
    p_apply.add_argument(
        "-c", "--changeset",
        type=str,
        required=True,
        help="Path to changeset YAML or JSON file",
    )
    p_apply.add_argument(
        "--status-dir",
        type=str,
        default=None,
        help="Directory for execution status files (optional)",
    )
    p_apply.add_argument("--execution-id", type=str, default=None, help="Execution id for status tracking")
    p_apply.add_argument(
        "--no-fail-fast",
        action="store_true",
        help="Continue applying after a failed change",
    )
    p_apply.set_defaults(handler=_cmd_apply)

    p_changeset_filter = sub.add_parser(
        "changset-filter",
        aliases=["changeset-filter"],
        help="Toggle apply flags in a changeset using filter rules",
    )
    _add_common_cli_options(p_changeset_filter)
    p_changeset_filter.add_argument(
        "--changeset-path",
        type=str,
        required=True,
        help="Path to changeset YAML or JSON file",
    )
    p_changeset_filter.add_argument(
        "--filter-rules",
        type=str,
        required=True,
        help="Filter rules as file path, file:// URI, or comma-separated rules",
    )
    p_changeset_filter.set_defaults(handler=_cmd_changeset_filter)

    args = parser.parse_args()
    setup_logging(
        "DEBUG" if bool(getattr(args, "debug", False)) else None,
        enable_console=bool(getattr(args, "console_logs", False)),
        log_file=getattr(args, "log_file", None),
        command_name=getattr(args, "command", None),
    )
    logger.info("Command started: %s", args.command)
    try:
        args.handler(args)
    except KeyboardInterrupt:
        logger.warning("Command interrupted by user")
        sys.exit(130)
    logger.info("Command finished: %s", args.command)


if __name__ == "__main__":
    main()
