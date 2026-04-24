import concurrent
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures import Future, ThreadPoolExecutor
from queue import Empty, Queue
import threading
from typing import Dict, List, MutableSequence, Optional, Any
from warnings import catch_warnings
from TM1py import TM1Service
import TM1py

from tm1_git_py.model import element
try:
    from tqdm import tqdm  # type: ignore[reportMissingModuleSource]
except Exception:  # pragma: no cover - optional runtime fallback
    tqdm = None

from functools import reduce

from tm1_git_py import filter as filter_module
from tm1_git_py.filter import EntityType, FilterRules, with_default_leaves_ignore, with_technical_objects_ignore
from tm1_git_py.model.chore import Chore
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.edge import Edge
from tm1_git_py.model.element import Element
from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.model import Model
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.model.process import Process
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.model_store import ModelStore
from tm1_git_py.model.subset import Subset
from tm1_git_py.model.task import Task
from tm1_git_py.model.ti import TI
from tm1_git_py.progress_reporting import (
    NoopProgressSink,
    ProgressEvent,
    ProgressKind,
    ProgressScope,
    ProgressSink,
    ProgressUnit,
)

from tm1_git_py.tm1py_ext import (
    get_cube_names,
    get_edges,
    get_edges_count,
    get_elements,
    get_elements_count,
    _get_elements_page,
    _get_edges_page,
    _get_subsets_page,
    get_process_names,
    get_subsets,
    get_subsets_count,
    get_views,
)
from tm1_git_py.tm1py_ext.dimension_service_ext import get_names as get_dimension_names
from tm1_git_py.tm1py_ext.hierarchy_service_ext import get_all_names as get_hierarchy_names


logger = logging.getLogger(__name__)

class ThreadSafeCounter:
    def __init__(self):
        self.big_total = 0
        self.lock = threading.Lock()

    def increment_by(self, value: int):
        with self.lock:  # Acquire lock before modifying the counter
            self.big_total += value

    def get_value(self):
        return self.big_total

def _default_max_workers() -> int:
    return max(1, ((os.cpu_count() or 1) // 2) + 1)


# class TqdmExportProgress(ProgressSink):
#     def __init__(self):
#         self._overall_bar = None
#         self._current_bar = None

#     def on_event(self, event: ProgressEvent) -> None:
#         if tqdm is None or not sys.stderr.isatty():
#             return
#         if self._overall_bar is None:
#             self._overall_bar = tqdm(
#                 total=1,
#                 desc="Export overall",
#                 unit="row",
#                 leave=False,
#                 dynamic_ncols=True,
#                 position=0,
#             )
#         if self._current_bar is None:
#             self._current_bar = tqdm(
#                 total=1,
#                 desc="Current",
#                 unit="row",
#                 leave=False,
#                 dynamic_ncols=True,
#                 position=1,
#             )

#         if event.scope == ProgressScope.TOTAL:
#             if event.kind == ProgressKind.START:
#                 total = max(1, int(event.total or 0))
#                 current = min(max(0, int(event.current or 0)), total)
#                 self._overall_bar.reset(total=total)
#                 self._overall_bar.unit = event.unit.value
#                 self._overall_bar.set_description_str(event.message or "Export overall", refresh=True)
#                 self._overall_bar.n = current
#                 self._overall_bar.refresh()
#                 return
#             if event.kind == ProgressKind.UPDATE:
#                 if event.total is not None:
#                     new_total = max(1, int(event.total))
#                     if int(self._overall_bar.total or 1) != new_total:
#                         self._overall_bar.reset(total=new_total)
#                 total = int(self._overall_bar.total or 1)
#                 self._overall_bar.n = min(max(0, int(event.current or 0)), total)
#                 self._overall_bar.refresh()
#                 return
#         if event.scope == ProgressScope.WORKER:
#             if event.kind == ProgressKind.START:
#                 total = max(1, int(event.total or 0))
#                 current = min(max(0, int(event.current or 0)), total)
#                 self._current_bar.reset(total=total)
#                 self._current_bar.unit = event.unit.value
#                 self._current_bar.set_description_str(event.message or "Current", refresh=True)
#                 self._current_bar.n = current
#                 self._current_bar.refresh()
#                 return
#             if event.kind == ProgressKind.UPDATE:
#                 if event.total is not None:
#                     new_total = max(1, int(event.total))
#                     if int(self._current_bar.total or 1) != new_total:
#                         self._current_bar.reset(total=new_total)
#                 total = int(self._current_bar.total or 1)
#                 self._current_bar.n = min(max(0, int(event.current or 0)), total)
#                 self._current_bar.refresh()
#                 return

#     def close(self) -> None:
#         if self._current_bar is not None:
#             self._current_bar.close()
#             self._current_bar = None
#         if self._overall_bar is not None:
#             self._overall_bar.close()
#             self._overall_bar = None


def _emit_progress_event(progress_sink: ProgressSink, event: ProgressEvent) -> None:
    progress_sink.on_event(event)


def export(
    tm1_conn: TM1Service,
    model_id: str,
    filter_rules_list: Optional[list[str]] = None,
    *,
    serialize: bool = False,
    model_output_dir: Optional[str] = None,
    progress_sink: Optional[ProgressSink] = None,
    max_workers: Optional[int] = None,
) -> tuple[Model, Dict[str, str]]:
    active_progress_sink: ProgressSink = progress_sink if progress_sink is not None else NoopProgressSink()
    include_progress_kwarg = progress_sink is not None
    effective_model_output_dir = model_output_dir or str(model_id)

    logger.info("TM1 export started")
    effective_rules = with_default_leaves_ignore(filter_rules_list)
    effective_rules.extend(with_technical_objects_ignore(filter_rules_list))
    filter_rules = FilterRules(effective_rules)
    logger.info(
        "Export filters configured additional_rules=%d effective_rules=%d",
        len(filter_rules_list or []),
        len(effective_rules),
    )

    try:
        if include_progress_kwarg:
            _dimensions, _dim_errors = dimensions_to_model(
                tm1_conn,
                model_id=model_id,
                filter_rules=filter_rules,
                serialize=serialize,
                model_output_dir=effective_model_output_dir,
                progress_sink=active_progress_sink,
                max_workers=max_workers,
            )
            _cubes, _cube_errors = cubes_to_model(
                tm1_conn,
                _dimensions,
                filter_rules=filter_rules,
                progress_sink=active_progress_sink,
            )
            _processes, _process_errors = procs_to_model(
                tm1_conn,
                filter_rules=filter_rules,
                progress_sink=active_progress_sink,
            )
            _chores, _chore_errors = chores_to_model(
                tm1_conn,
                filter_rules=filter_rules,
                progress_sink=active_progress_sink,
            )
        else:
            _dimensions, _dim_errors = dimensions_to_model(
                tm1_conn,
                model_id=model_id,
                filter_rules=filter_rules,
                serialize=serialize,
                model_output_dir=effective_model_output_dir,
                max_workers=max_workers,
            )
            _cubes, _cube_errors = cubes_to_model(
                tm1_conn,
                _dimensions,
                filter_rules=filter_rules,
            )
            _processes, _process_errors = procs_to_model(
                tm1_conn,
                filter_rules=filter_rules,
            )
            _chores, _chore_errors = chores_to_model(
                tm1_conn,
                filter_rules=filter_rules,
            )
    finally:
        active_progress_sink.close()

    _model = Model(
        cubes=list(_cubes.values()),
        dimensions=list(_dimensions.values()),
        processes=list(_processes.values()),
        chores=list(_chores.values()),
        model_id=model_id,
    )
    logger.info(
        "TM1 export model assembled dimensions=%d cubes=%d processes=%d chores=%d",
        len(_model.dimensions),
        len(_model.cubes),
        len(_model.processes),
        len(_model.chores),
    )
    _errors = {}
    _errors['dim'] = _dim_errors
    _errors['cube'] = _cube_errors
    _errors['process'] = _process_errors
    _errors['chore'] = _chore_errors

    total_errors = sum(len(category_errors) for category_errors in _errors.values())
    if total_errors:
        logger.warning(
            "TM1 export completed with errors: dimensions=%d cubes=%d processes=%d chores=%d",
            len(_dim_errors),
            len(_cube_errors),
            len(_process_errors),
            len(_chore_errors),
        )
    else:
        logger.info("TM1 export completed without errors")

    return _model, _errors


def chores_to_model(
    tm1_conn,
    filter_rules: FilterRules,
    progress_sink: Optional[ProgressSink] = None,
) -> tuple[Dict[str, Chore], Dict[str, str]]:
    progress_sink = progress_sink if progress_sink is not None else NoopProgressSink()
    all_chores = tm1_conn.chores.get_all_names()
    _chores: Dict[str, Chore] = {}
    _errors: Dict[str, str] = {}
    skipped_chores = 0
    skipped_tasks = 0
    logger.info("Exporting %d chores", len(all_chores))
    _emit_progress_event(
        progress_sink,
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.WORKER,
            current=0,
            total=len(all_chores),
            unit=ProgressUnit.LINE,
            message="exporting chores",
        ),
    )

    for idx, chore_name in enumerate(all_chores, start=1):
        try:
            chore_url = Chore.uri_for(chore_name)
            if filter_rules.should_exclude(chore_url):
                logger.debug("Skipping chore by filter: %s", chore_url)
                skipped_chores += 1
                continue

            chore = tm1_conn.chores.get(chore_name=chore_name)

            tasks_for_model = []
            for tm1py_task in chore.tasks:
                task_dict = tm1py_task.body_as_dict
                process_name = ""
                process_bind = task_dict.get("Process@odata.bind", "")
                match = re.search(r"Processes\('([^']*)'\)", process_bind)
                if match:
                    process_name = match.group(1)
                task_path = f"{chore_url}/Tasks('{process_name}')"
                if filter_rules.should_exclude(task_path):
                    logger.debug("Skipping chore task by filter: %s", task_path)
                    skipped_tasks += 1
                    continue

                task_obj = Task(
                    process_name=process_name,
                    parameters=task_dict.get('Parameters', [])
                )
                tasks_for_model.append(task_obj)

            _chore = Chore(
                name=chore.name,
                start_time=chore.start_time.start_time_string,
                dst_sensitive=chore.dst_sensitivity,
                active=chore.active,
                execution_mode=chore.execution_mode,
                frequency=chore.frequency.frequency_string,
                tasks=tasks_for_model,
            )
            _chores[chore.name] = _chore
        finally:
            _emit_progress_event(
                progress_sink,
                ProgressEvent.make(
                    kind=ProgressKind.UPDATE,
                    scope=ProgressScope.WORKER,
                    current=idx,
                    total=len(all_chores),
                    unit=ProgressUnit.LINE,
                    message="exporting chores",
                ),
            )

    logger.info(
        "Chore export assembly finished total=%d kept=%d skipped_chores=%d skipped_tasks=%d",
        len(all_chores),
        len(_chores),
        skipped_chores,
        skipped_tasks,
    )
    _emit_progress_event(
        progress_sink,
        ProgressEvent.make(
            kind=ProgressKind.UPDATE,
            scope=ProgressScope.WORKER,
            current=len(all_chores),
            total=len(all_chores),
            unit=ProgressUnit.LINE,
            message="exporting chores",
        ),
    )
    return _chores, _errors


def procs_to_model(
    tm1_conn :TM1Service,
    filter_rules: FilterRules,
    progress_sink: Optional[ProgressSink] = None,
) -> tuple[Dict[str, Process], Dict[str, str]]:
    progress_sink = progress_sink if progress_sink is not None else NoopProgressSink()
    processes_tm1_filter = filter_rules.to_tm1_name_filter(EntityType.PROCESS)
    filtered_process_names = [] if processes_tm1_filter.skip_all else get_process_names(
            tm1_conn,
            filter=processes_tm1_filter.filter_expr
    )

    _processes: Dict[str, Process] = {}
    _errors: Dict[str, str] = {}
    logger.info("Exporting %d processes", len(filtered_process_names))
    _emit_progress_event(
        progress_sink,
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.WORKER,
            current=0,
            total=len(filtered_process_names),
            unit=ProgressUnit.LINE,
            message="exporting processes",
        ),
    )
    for idx, process_name in enumerate(filtered_process_names, start=1):
        try:
            process = tm1_conn.processes.get(name_process=process_name)

            _ti = TI(prolog_procedure=process.prolog_procedure,
                     metadata_procedure=process.metadata_procedure,
                     data_procedure=process.data_procedure,
                     epilog_procedure=process.epilog_procedure)
            _process = Process(name=process.name, hasSecurityAccess=process.has_security_access,
                               code_link=process_name + '.ti',
                               datasource='',
                               parameters=process.parameters, variables=process.variables, ti=_ti)
            _processes[process.name] = _process
        finally:
            _emit_progress_event(
                progress_sink,
                ProgressEvent.make(
                    kind=ProgressKind.UPDATE,
                    scope=ProgressScope.WORKER,
                    current=idx,
                    total=len(filtered_process_names),
                    unit=ProgressUnit.LINE,
                    message="exporting processes",
                ),
            )
    logger.info(
        "Process export assembly finished total=%d kept=%d",
        len(filtered_process_names),
        len(_processes)
    )
    _emit_progress_event(
        progress_sink,
        ProgressEvent.make(
            kind=ProgressKind.UPDATE,
            scope=ProgressScope.WORKER,
            current=len(filtered_process_names),
            total=len(filtered_process_names),
            unit=ProgressUnit.LINE,
            message="exporting processes",
        ),
    )
    return _processes, _errors


def cubes_to_model(
    tm1_conn: TM1Service,
    _dimensions: Dict[str, Dimension],
    filter_rules: FilterRules,
    progress_sink: Optional[ProgressSink] = None,
) -> tuple[Dict[str, Cube], Dict[str, str]]:
    progress_sink = progress_sink if progress_sink is not None else NoopProgressSink()
    cubes_tm1_filter = filter_rules.to_tm1_name_filter(EntityType.CUBE)
    filtered_cube_names = [] if cubes_tm1_filter.skip_all else get_cube_names(
        tm1_conn,
        filter=cubes_tm1_filter.filter_expr,
    )

    _cubes: Dict[str, Cube] = {}
    _errors: Dict[str, str] = {}
    skipped_rules = 0
    skipped_views = 0
    logger.info("Exporting %d cubes", len(filtered_cube_names))
    _emit_progress_event(
        progress_sink,
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.WORKER,
            current=0,
            total=len(filtered_cube_names),
            unit=ProgressUnit.LINE,
            message="exporting cubes",
        ),
    )

    for idx, cube_name in enumerate(filtered_cube_names, start=1):
        
        try:
            cube = tm1_conn.cubes.get(cube_name=cube_name)

            rule_text = ""
            if cube.has_rules:
                raw_body = cube.rules.body
                try:
                    rule_data = json.loads(raw_body)
                    rule_text = rule_data.get("Rules", "")
                except (json.JSONDecodeError, AttributeError):
                    rule_text = raw_body if isinstance(raw_body, str) else ""

            rules_list = _parse_rules(rule_text)
            filtered_rules_list = []
            for rule in rules_list:
                rule_path = f"{Rule.uri_for(cube_name)}|{filter_module.normalize_for_path(rule.area)}"
                if filter_rules.should_exclude(rule_path):
                    logger.debug("Skipping rule by filter: %s", rule_path)
                    skipped_rules += 1
                    continue
                filtered_rules_list.append(rule)
            _cube = Cube(
                name=cube_name,
                dimensions=[],
                rules=filtered_rules_list,
                views=[],
            )

            skip_cube_due_to_filtered_dimension = False
            if cube.dimensions:
                for dimension in cube.dimensions:
                    _dimension = _dimensions.get(dimension)
                    if not _dimension:
                        dimension_url = Dimension.uri_for(dimension)
                        if filter_rules.should_exclude(dimension_url):
                            logger.debug(
                                "Skipping cube '%s' because dependent dimension is filtered: %s",
                                cube_name,
                                dimension_url,
                            )
                            skip_cube_due_to_filtered_dimension = True
                            break
                        logger.warning(
                            "Cube '%s' references missing dimension '%s'",
                            cube_name,
                            dimension,
                        )
                        _errors[cube_name] = f"Dimension '{dimension}' not found"
                        skip_cube_due_to_filtered_dimension = True
                        break
                    else:
                        _cube.dimensions.append(_dimension)


            _cubes[cube_name] = _cube

            views_tm1_filter = filter_rules.to_tm1_child_name_filter(
                parent_chain=[(EntityType.CUBE, cube_name)],
                child_entity_type=EntityType.VIEW,
            )
            filtered_view_tuples = get_views(
                tm1_conn,
                cube_name=cube_name,
                filter=views_tm1_filter.filter_expr,
            )
            if filtered_view_tuples:
                private_views, public_views = filtered_view_tuples
                for view in private_views + public_views:
                    if isinstance(view, TM1py.Objects.MDXView):
                        _view = MDXView(
                            name=view.name,
                            mdx=view.mdx,
                        )
                    elif isinstance(view, TM1py.Objects.NativeView):
                        _view = NativeView(
                            name=view.name,
                            columns=view.columns,
                            rows=view.rows,
                            titles=view.titles,
                            suppress_empty_columns=view.suppress_empty_columns,
                            suppress_empty_rows=view.suppress_empty_rows,
                            format_string=view.format_string,
                        )
                    else:
                        continue
                    _cube.views.append(_view)


        except Exception as e:
            logger.error("Failed to export cube '%s'", cube_name, exc_info=True)
            _errors[cube_name] = str(e)
        finally:
            _emit_progress_event(
                progress_sink,
                ProgressEvent.make(
                    kind=ProgressKind.UPDATE,
                    scope=ProgressScope.WORKER,
                    current=idx,
                    total=len(filtered_cube_names),
                    unit=ProgressUnit.LINE,
                    message="exporting cubes",
                ),
            )

    logger.info(
        "Cube export assembly finished total=%d kept=%d skipped_rules=%d skipped_views=%d",
        len(filtered_cube_names),
        len(_cubes),
        skipped_rules,
        skipped_views,
    )
    _emit_progress_event(
        progress_sink,
        ProgressEvent.make(
            kind=ProgressKind.UPDATE,
            scope=ProgressScope.WORKER,
            current=len(filtered_cube_names),
            total=len(filtered_cube_names),
            unit=ProgressUnit.LINE,
            message="exporting cubes",
        ),
    )
    return _cubes, _errors


def dimensions_to_model(
    tm1_conn: TM1Service,
    model_id: str,
    filter_rules: FilterRules,
    *,
    serialize: bool = False,
    model_output_dir: Optional[str] = None,
    progress_sink: Optional[ProgressSink] = None,
    max_workers: Optional[int] = None,
) -> tuple[Dict[str, Dimension], Dict[str, str]]:
    progress_sink = progress_sink if progress_sink is not None else NoopProgressSink()
    dimensions_tm1_filter = filter_rules.to_tm1_name_filter(EntityType.DIMENSION)
    all_dims = [] if dimensions_tm1_filter.skip_all else get_dimension_names(
        tm1_conn,
        filter=dimensions_tm1_filter.filter_expr,
    )
    _errors: Dict[str, str] = {}
    _dimensions: Dict[str, Dimension] = {}
    model_store = ModelStore.for_model_id(model_id)

    big_total = ThreadSafeCounter()
    # group key: (dimension, hierarchy, object_group)
    group_totals: Dict[tuple[str, str, str], Optional[int]] = {}
    group_processed: Dict[tuple[str, str, str], int] = {}
    group_complete_waiting_total: set[tuple[str, str, str]] = set()
    count_queue: Queue[tuple[tuple[str, str, str], Optional[int], Optional[Exception]]] = Queue()
    overall_total_rows = 0
    overall_processed_rows = 0
    io_workers = max(1, int(max_workers if max_workers is not None else _default_max_workers()))

    def _emit_overall_update() -> None:
        _emit_progress_event(
            progress_sink,
            ProgressEvent.make(
                kind=ProgressKind.UPDATE,
                scope=ProgressScope.TOTAL,
                current=overall_processed_rows,
                total=overall_total_rows,
                unit=ProgressUnit.LINE,
                message="Export overall",
            ),
        )

    def _register_group_total(group_key: tuple[str, str, str], total: int) -> None:
        nonlocal overall_total_rows, overall_processed_rows
        normalized_total = max(0, int(total))
        prior_total = group_totals.get(group_key)
        prior_effective = 0 if prior_total is None else int(prior_total)
        current_processed = int(group_processed.get(group_key, 0))
        if normalized_total < current_processed:
            normalized_total = current_processed
        delta_total = normalized_total - prior_effective
        if delta_total != 0:
            overall_total_rows += delta_total
        group_totals[group_key] = normalized_total
        if group_key in group_complete_waiting_total:
            missing = normalized_total - current_processed
            if missing > 0:
                group_processed[group_key] = current_processed + missing
                overall_processed_rows += missing
            group_complete_waiting_total.discard(group_key)
        _emit_overall_update()

    def _advance_group_processed(group_key: tuple[str, str, str], delta_rows: int) -> None:
        nonlocal overall_processed_rows
        if delta_rows <= 0:
            return
        group_processed[group_key] = int(group_processed.get(group_key, 0)) + int(delta_rows)
        overall_processed_rows += int(delta_rows)
        _emit_overall_update()

    def _mark_group_complete(group_key: tuple[str, str, str]) -> None:
        total = group_totals.get(group_key)
        processed = int(group_processed.get(group_key, 0))
        if total is None:
            group_complete_waiting_total.add(group_key)
            return
        missing = int(total) - processed
        if missing > 0:
            _advance_group_processed(group_key, missing)

    def _drain_count_queue() -> None:
        while True:
            try:
                group_key, total, error = count_queue.get_nowait()
            except Empty:
                break
            if error is not None:
                logger.warning("Failed to collect count for %s: %s", group_key, error)
                _register_group_total(group_key, int(group_processed.get(group_key, 0)))
            else:
                _register_group_total(group_key, int(total or 0))

    def _start_current(activity: str, total_rows: int) -> None:
        _emit_progress_event(
            progress_sink,
            ProgressEvent.make(
                kind=ProgressKind.START,
                scope=ProgressScope.WORKER,
                current=0,
                total=total_rows,
                unit=ProgressUnit.LINE,
                message=activity,
            ),
        )

    def _update_current(activity: str, current_rows: int, total_rows: int) -> None:
        _emit_progress_event(
            progress_sink,
            ProgressEvent.make(
                kind=ProgressKind.UPDATE,
                scope=ProgressScope.WORKER,
                current=current_rows,
                total=total_rows,
                unit=ProgressUnit.LINE,
                message=activity,
            ),
        )

    def _complete_current(activity: str, current_rows: int, total_rows: int) -> None:
        _emit_progress_event(
            progress_sink,
            ProgressEvent.make(
                kind=ProgressKind.UPDATE,
                scope=ProgressScope.WORKER,
                current=current_rows,
                total=total_rows,
                unit=ProgressUnit.LINE,
                message=activity,
            ),
        )

    def _submit_count_jobs(
        executor: ThreadPoolExecutor,
        dim_name: str,
        hierarchy_name: str,
        elements_filter_expr: Optional[str],
        subsets_filter_expr: Optional[str],
        edges_filter_expr: Optional[str],
        elements_skip_all: bool,
        subsets_skip_all: bool,
        edges_skip_all: bool,
    ) -> None:
        def _submit(group_name: str, fn, *args, **kwargs) -> None:
            key = (dim_name, hierarchy_name, group_name)
            group_totals[key] = None
            group_processed[key] = 0
            activity = f"collecting count {group_name} {dim_name}/{hierarchy_name}"
            logger.info("%s", activity)
            _emit_progress_event(
                progress_sink,
                ProgressEvent.make(
                    kind=ProgressKind.UPDATE,
                    scope=ProgressScope.WORKER,
                    current=0,
                    total=1,
                    unit=ProgressUnit.LINE,
                    message=activity,
                ),
            )

            def _runner() -> int:
                return int(fn(*args, **kwargs))

            future = executor.submit(_runner)

            def _done_callback(done_future):
                try:
                    value = int(done_future.result())
                    count_queue.put((key, value, None))
                except Exception as ex:
                    count_queue.put((key, None, ex))

            future.add_done_callback(_done_callback)

        if elements_skip_all:
            _register_group_total((dim_name, hierarchy_name, "elements"), 0)
        else:
            _submit(
                "elements",
                get_elements_count,
                tm1_conn,
                dim_name,
                hierarchy_name,
                filter=elements_filter_expr,
            )
        if subsets_skip_all:
            _register_group_total((dim_name, hierarchy_name, "subsets"), 0)
        else:
            _submit(
                "subsets",
                get_subsets_count,
                tm1_conn,
                dimension_name=dim_name,
                hierarchy_name=hierarchy_name,
                filter=subsets_filter_expr,
            )
        if edges_skip_all:
            _register_group_total((dim_name, hierarchy_name, "edges"), 0)
        else:
            _submit(
                "edges",
                get_edges_count,
                tm1_conn,
                dim_name,
                hierarchy_name,
                filter=edges_filter_expr,
            )

    logger.info("Exporting %d dimensions", len(all_dims))
    _emit_progress_event(
        progress_sink,
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.TOTAL,
            current=0,
            total=0,
            unit=ProgressUnit.LINE,
            message="Export overall",
        ),
    )
    _emit_progress_event(
        progress_sink,
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.WORKER,
            current=0,
            total=len(all_dims),
            unit=ProgressUnit.LINE,
            message="exporting dimensions",
        ),
    )

    
    with ThreadPoolExecutor(max_workers=io_workers, thread_name_prefix="dim_io_executor") as io_executor:

        total = 0;
        for dim_index, dim_name in enumerate(all_dims, start=1):
            try:
                hierarchies_tm1_filter = filter_rules.to_tm1_hierarchy_name_filter(dim_name)
                hierarchy_identities = [] if hierarchies_tm1_filter.skip_all else get_hierarchy_names(tm1_conn, dim_name, filter=hierarchies_tm1_filter.filter_expr)
                hierarchy_list: List[Hierarchy] = []

                hierarchy_futures = []
                hierarchy_names = {}

                for idx, hierarchy_identity in enumerate(hierarchy_identities):
                    hierarchy_name = hierarchy_identity.name
                    incoming_hierarchy_etag = hierarchy_identity.etag
                    elements_tm1_filter = filter_rules.to_tm1_element_name_filter(dim_name, hierarchy_name)
                    subsets_tm1_filter = filter_rules.to_tm1_subset_name_filter(dim_name, hierarchy_name)
                    edges_tm1_filter = filter_rules.to_tm1_edge_name_filter(dim_name, hierarchy_name)

                    can_reuse_elements = False
                    can_reuse_subsets = False
                    can_reuse_edges = False
                    if incoming_hierarchy_etag is not None:
                        existing_elements_etag, existing_elements_rules = model_store.get_group_reuse_metadata(model_id=model_id, dimension_name=dim_name, hierarchy_name=hierarchy_name, object_type="elements")
                        existing_subsets_etag, existing_subsets_rules = model_store.get_group_reuse_metadata(model_id=model_id, dimension_name=dim_name, hierarchy_name=hierarchy_name, object_type="subsets")
                        existing_edges_etag, existing_edges_rules = model_store.get_group_reuse_metadata(model_id=model_id, dimension_name=dim_name, hierarchy_name=hierarchy_name, object_type="edges")
                        can_reuse_elements = (existing_elements_etag == incoming_hierarchy_etag and existing_elements_rules == elements_tm1_filter.applicable_rules)
                        can_reuse_subsets = (existing_subsets_etag == incoming_hierarchy_etag and existing_subsets_rules == subsets_tm1_filter.applicable_rules)
                        can_reuse_edges = (existing_edges_etag == incoming_hierarchy_etag and existing_edges_rules == edges_tm1_filter.applicable_rules)

                    hierarchy = Hierarchy(
                            name=hierarchy_name,
                            dimension_name=dim_name,
                            model_id=model_id,
                            model_output_dir=model_output_dir,
                            serialize=serialize,
                            hierarchy_etag=incoming_hierarchy_etag,
                            reuse_existing_store=True,
                            elements_filter_rules=elements_tm1_filter.applicable_rules,
                            edges_filter_rules=edges_tm1_filter.applicable_rules,
                            subsets_filter_rules=subsets_tm1_filter.applicable_rules,
                        )
                    hierarchy_names[hierarchy_name] = hierarchy

                    if not can_reuse_elements and hasattr(hierarchy.elements, "replace_with_payloads"):
                        hierarchy.elements.replace_with_payloads(())
                    if not can_reuse_subsets and hasattr(hierarchy.subsets, "replace_with_payloads"):
                        hierarchy.subsets.replace_with_payloads(())
                    if not can_reuse_edges and hasattr(hierarchy.edges, "replace_with_payloads"):
                        hierarchy.edges.replace_with_payloads(())

                    def _start_page(fn, tm1_conn, dim_name, hierarchy_name, filter_expr, skip, top, total, mutable_list: MutableSequence):
                        progress_sink.on_event(ProgressEvent.worker_line(current=0, total=1, message=f"counting hierarchies ({dim_name}), page {skip} of {total}"))
                        page = fn(tm1_conn=tm1_conn, dimension_name=dim_name, hierarchy_name=hierarchy_name, filter=filter_expr, skip=skip, top=top, count=True)
                        progress_sink.on_event(ProgressEvent.worker_line(current=1, total=1))    
                        return (page, mutable_list)

                    # parallel fetching of elements pages
                    elements_count = get_elements_count(tm1_conn, dim_name, hierarchy_name, filter=elements_tm1_filter.filter_expr)
                    big_total.increment_by(elements_count)
                    futures = []
                    progress_sink.on_event(ProgressEvent.total_line(total=big_total.get_value()))
                    if not can_reuse_elements and not elements_tm1_filter.skip_all:
                        for i in range(0, elements_count, 100_000):
                            future = io_executor.submit(_start_page, _get_elements_page, tm1_conn, dim_name, hierarchy_name, elements_tm1_filter.filter_expr, i, 100_000, elements_count, hierarchy.elements)
                            futures.append(future)
                    else:
                        progress_sink.on_event(ProgressEvent.total_line(current_delta=elements_count))

                    # parallel fetching of edge pages
                    edges_count = get_edges_count(tm1_conn, dim_name, hierarchy_name, filter=edges_tm1_filter.filter_expr)
                    big_total.increment_by(edges_count)
                    progress_sink.on_event(ProgressEvent.total_line(total=big_total.get_value()))
                    if not can_reuse_edges and not edges_tm1_filter.skip_all:
                        for i in range(0, edges_count, 100_000):
                            future = io_executor.submit(_start_page, _get_edges_page, tm1_conn, dim_name, hierarchy_name, edges_tm1_filter.filter_expr, i, 100_000, edges_count, hierarchy.edges)
                            futures.append(future)
                    else:
                        progress_sink.on_event(ProgressEvent.total_line(current_delta=edges_count))

                    # parallel fetching of subset pages
                    subsets_count = get_subsets_count(tm1_conn, dim_name, hierarchy_name, filter=subsets_tm1_filter.filter_expr)
                    big_total.increment_by(subsets_count)
                    progress_sink.on_event(ProgressEvent.total_line(total=big_total.get_value()))
                    if not can_reuse_subsets and not subsets_tm1_filter.skip_all:
                        for i in range(0, subsets_count, 100_000):
                            future = io_executor.submit(_start_page, _get_subsets_page, tm1_conn, dim_name, hierarchy_name, subsets_tm1_filter.filter_expr, i, 100_000, subsets_count, hierarchy.subsets)
                            futures.append(future)
                    else:
                        progress_sink.on_event(ProgressEvent.total_line(current_delta=subsets_count))

                    # write to sqlite on main (single thread)
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            page, collector = future.result()
                            merged_count = len(page.objects)
                            merge_started = time.perf_counter()
                            if hasattr(collector, "extend_payloads"):
                                collector.extend_payloads(page.raw_rows)
                                merge_mode = "payload"
                            else:
                                collector.extend(page.objects)
                                merge_mode = "object"
                            logger.info(
                                "Hierarchy page merge completed dimension='%s' hierarchy='%s' rows=%d mode=%s elapsed_ms=%.3f",
                                dim_name,
                                hierarchy_name,
                                merged_count,
                                merge_mode,
                                (time.perf_counter() - merge_started) * 1000,
                            )
                            progress_sink.on_event(ProgressEvent.total_line(current_delta=merged_count))
                        except Exception as exc:
                            print(f'Task generated an exception: {exc}')

                    def _start_serialize_hierarchy(hierarchy: Hierarchy):
                        progress_sink.on_event(ProgressEvent.worker_line(current=0, total=1, message=f"Dumping hierarchy to JSON"))
                        hierarchy.serialize_hierarchy_json()
                        return hierarchy.name

                    hierarchy_futures.append(io_executor.submit(_start_serialize_hierarchy, hierarchy))

                for future in concurrent.futures.as_completed(hierarchy_futures):
                    try:
                        hierarchy_name = future.result()
                        hierarchy = hierarchy_names[hierarchy_name]
                        progress_sink.on_event(ProgressEvent.worker_line(current=1, total=1, message=f"Recalculating hash and signature"))
                        hierarchy.finalize()
                        progress_sink.on_event(ProgressEvent.worker_line(current=1, total=1, message=f"Recalculating hash and signature"))
                        hierarchy_list.append(hierarchy)
                    except Exception as exc:
                        print(f'Task generated an exception: {exc}')

                if len(hierarchy_list) > 0:
                    _dimension = Dimension(
                        name=dim_name,
                        hierarchies=hierarchy_list,
                        defaultHierarchy=hierarchy_list[0],
                    )
                else:
                    empty_hierarchy = Hierarchy(name=dim_name, elements=[], edges=[], subsets=[])
                    _dimension = Dimension(
                        name=dim_name,
                        hierarchies=[empty_hierarchy],
                        defaultHierarchy=empty_hierarchy,
                    )
                _dimensions[dim_name] = _dimension
            except Exception as e:
                logger.error("Failed to export dimension '%s'", dim_name, exc_info=True)
                _errors[dim_name] = str(e)
            finally:
                _drain_count_queue()
                _emit_progress_event(
                    progress_sink,
                    ProgressEvent.make(
                        kind=ProgressKind.UPDATE,
                        scope=ProgressScope.WORKER,
                        current=dim_index,
                        total=len(all_dims),
                        unit=ProgressUnit.LINE,
                        message="exporting dimensions",
                    ),
                )   
    return _dimensions, _errors


def _parse_rules(rule_text: str) -> List[Rule]:
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


def server_configs_to_model(tm1_conn: TM1Service) -> Dict:
    configs = tm1_conn.configuration.get_active()
    return configs
