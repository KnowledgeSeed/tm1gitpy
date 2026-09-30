import copy
import importlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Iterable, Optional, Union, TypeVar, Literal

from TM1py import TM1Service, Process as TM1pyProcess
from requests import Response

from tm1_git_py import Changeset
from tm1_git_py.model import (
    Cube,
    MDXView,
    Dimension,
    Hierarchy,
    Subset,
    Process,
    Chore,
    Element,
    Edge,
    Rule,
)
from tm1_git_py.model.rule import DEFAULT_RULE_NAME, _rule_segment_sort_key
from tm1_git_py.reporting.progress_reporting import (
    NoopProgressSink,
    ProgressEvent,
    ProgressKind,
    ProgressScope,
    ProgressSink,
    ProgressUnit,
)
from tm1_git_py.services.changeset import ChangeType, Change, ObjectType
from tm1_git_py.services.changeset_status import ChangeSetStatusStore
from tm1_git_py.services.filter import (
    DEFAULT_TM1_TECHNICAL_OBJECTS,
    should_exclude_path,
)

logger = logging.getLogger(__name__)

T = TypeVar(
    "T",
    Cube,
    MDXView,
    Dimension,
    Hierarchy,
    Subset,
    Process,
    Chore,
    Element,
    Edge,
    Rule,
)


def _normalize_apply_response(
    resp,
    *,
    action_name: str,
    obj_type: str,
    obj_name: str,
    obj_path: str,
) -> Response:
    if isinstance(resp, Response):
        return resp

    normalized = Response()
    normalized.status_code = 200
    normalized.url = obj_path or f"{obj_type}:{obj_name}"
    normalized._content = (
        f"{action_name} {obj_type}{':' + obj_name if obj_name else ''} "
        f"completed without explicit HTTP response."
    ).encode("utf-8")
    normalized.encoding = "utf-8"
    return normalized


def _build_ignored_duplicate_create_response(
    *,
    object_type: str,
    object_name: str,
    uri: Optional[str],
) -> Response:
    response = Response()
    response.status_code = 208
    response.url = uri or f"{object_type}:{object_name}"
    response._content = (
        f"Skipped create for existing technical object "
        f"{object_type}{':' + object_name if object_name else ''}."
    ).encode("utf-8")
    response.encoding = "utf-8"
    return response


def _is_technical_object_uri(
    uri: Optional[str], object_type: str, object_name: str
) -> bool:
    if uri:
        try:
            return should_exclude_path(uri, DEFAULT_TM1_TECHNICAL_OBJECTS)
        except Exception:
            logger.debug(
                "Failed to classify technical object from uri='%s'", uri, exc_info=True
            )
    return object_type in {"Cube", "Dimension", "Process"} and (
        object_name or ""
    ).startswith("}")


def _exception_status_code(exc: Exception) -> Optional[int]:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    return None


def _is_duplicate_create_exception(exc: Exception) -> bool:
    normalized_text = str(exc or "").lower()
    status_code = _exception_status_code(exc)
    return "already exists" in normalized_text and status_code in {400, 409}


def _build_skipped_rule_response(uri: Optional[str], reason: str) -> Response:
    response = Response()
    response.status_code = 208
    response.url = uri or ""
    response._content = f"Skipped rule change: {reason}.".encode("utf-8")
    response.encoding = "utf-8"
    return response


@dataclass
class _RuleFold:
    """How the rule changes of each cube are unified, keyed by execution index.

    TM1 keeps one rule text per cube, so the selected Rule changes of a cube
    with rule regions are folded into one cube write. That write runs at the
    first execution row of the cube (its Cube change or one of its Rule
    changes); the cube's other rows record the same result, so status rows and
    progress stay one per change.
    """

    run_by_index: dict[int, Change] = field(default_factory=dict)
    result_from_index: dict[int, int] = field(default_factory=dict)
    skipped_by_index: dict[int, str] = field(default_factory=dict)
    failed_by_index: dict[int, str] = field(default_factory=dict)


def _copy_cube_change(change: Change, rules: list[Rule]) -> Change:
    body = copy.copy(change.body)
    body.rules = rules
    return replace(change, body=body)


def _rule_delta(rule_changes: list[Change]) -> list[Rule]:
    delta = []
    for change in rule_changes:
        rule = change.body
        if change.change_type == ChangeType.REMOVE:
            rule = Rule(area=rule.area, full_statement="", comment="", name=rule.name)
        delta.append(rule)
    return delta


def _fold_rule_changes(
    execution_changes: list[Change], deselected_cube_adds: set[str]
) -> _RuleFold:
    """Unify the selected Rule changes of each cube into its Cube CRUD step.

    - Rule changes of a removed cube are skipped.
    - A created cube with rule regions gets exactly the selected Rule ADD
      bodies; a created cube with an unmarked ``default`` rule keeps its full
      rule text and its Rule changes are skipped.
    - For any other cube with at least one region change, the selected Rule
      changes become a delta (REMOVE -> empty text) that ``update_cube`` merges
      onto the target's current rules. The delta rides on a copy of the Cube
      MODIFY, or on a synthetic Cube MODIFY when the changeset has none.
    - A Cube MODIFY without region changes gets no rules, so it never rewrites
      the rules. Cubes with only ``default`` Rule changes and drillthrough rules
      keep the per-change path.

    The incoming Change objects and their bodies are never modified.
    """
    fold = _RuleFold()
    cube_index_by_name: dict[str, int] = {}
    rule_indexes_by_cube: dict[str, list[int]] = {}
    for index, change in enumerate(execution_changes):
        if change.object_type == ObjectType.CUBE:
            cube_index_by_name[change.body.name] = index
        elif change.object_type == ObjectType.RULE:
            cube_name = Rule.cube_name_from_uri(change.uri)
            if cube_name:
                rule_indexes_by_cube.setdefault(cube_name, []).append(index)

    for cube_name in cube_index_by_name.keys() | rule_indexes_by_cube.keys():
        cube_index = cube_index_by_name.get(cube_name)
        cube_change = execution_changes[cube_index] if cube_index is not None else None
        rule_indexes = rule_indexes_by_cube.get(cube_name, [])
        rule_changes = [execution_changes[i] for i in rule_indexes]
        has_regions = any(change.body.name != DEFAULT_RULE_NAME for change in rule_changes)

        if cube_change is None and cube_name in deselected_cube_adds:
            for index in rule_indexes:
                fold.failed_by_index[index] = (
                    f"Cannot apply rule change {execution_changes[index].uri}: "
                    f"cube '{cube_name}' does not exist on the target because its "
                    f"create is not selected"
                )
            continue

        if cube_change is not None and cube_change.change_type == ChangeType.REMOVE:
            for index in rule_indexes:
                fold.skipped_by_index[index] = f"cube '{cube_name}' is removed in the same changeset"
            continue

        if cube_change is not None and cube_change.change_type == ChangeType.ADD:
            body_rules = getattr(cube_change.body, "rules", None) or []
            if not has_regions and all(rule.name == DEFAULT_RULE_NAME for rule in body_rules):
                for index in rule_indexes:
                    fold.skipped_by_index[index] = (
                        f"cube '{cube_name}' is created with its full rule text in the same changeset"
                    )
                continue
            selected_rules = sorted(
                (change.body for change in rule_changes if change.change_type == ChangeType.ADD),
                key=lambda rule: _rule_segment_sort_key(rule.name),
            )
            fold.run_by_index[cube_index] = _copy_cube_change(cube_change, selected_rules)
            for index in rule_indexes:
                fold.result_from_index[index] = cube_index
            continue

        if not has_regions:
            if cube_change is not None:
                fold.run_by_index[cube_index] = _copy_cube_change(cube_change, [])
            continue

        delta = _rule_delta(rule_changes)
        if cube_change is not None:
            cube_write = _copy_cube_change(cube_change, delta)
        else:
            cube_write = Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.CUBE,
                uri=Cube.uri_for(cube_name),
                body=Cube(name=cube_name, dimensions=[], rules=delta, views=[]),
            )
        group_indexes = sorted(rule_indexes + ([cube_index] if cube_index is not None else []))
        writer_index = group_indexes[0]
        fold.run_by_index[writer_index] = cube_write
        for index in group_indexes[1:]:
            fold.result_from_index[index] = writer_index
        logger.info(
            "Folded %d rule change(s) of cube '%s' into one rules write",
            len(rule_changes), cube_name,
        )
    return fold


def apply(
    changeset: Changeset,
    tm1_service: TM1Service,
    *,
    status_dir: Optional[Union[str, Path]] = None,
    execution_id: Optional[str] = None,
    fail_fast: bool = True,
    progress_sink: Optional[ProgressSink] = None,
) -> tuple[bool, Union[list, None]]:
    progress = progress_sink if progress_sink is not None else NoopProgressSink()
    changes = []
    logger.info(
        "Starting apply changeset_id=%s fail_fast=%s changes=%d",
        changeset._changeset_id,
        fail_fast,
        len(changeset.changes),
    )
    if not changeset.has_changes():
        logger.info("No changes to apply.")
        return True, None

    execution_changes, rule_fold = _prepare_execution_changes(changeset.changes)
    logger.info("Prepared %d execution change(s)", len(execution_changes))
    if not execution_changes:
        logger.info("No executable changes after apply flag filtering.")
        return True, None
    total_operations = len(execution_changes)
    progress.on_event(
        ProgressEvent.make(
            kind=ProgressKind.START,
            scope=ProgressScope.TOTAL,
            unit=ProgressUnit.LINE,
            current=0,
            total=total_operations,
            message="applying changeset",
            path=changeset._changeset_id,
        )
    )
    """    
    validate_errors = validate_changeset(
        tm1_service=tm1_service,
        changeset_object=changeset,
        fail_fast=fail_fast
    )
    if validate_errors:
        logger.warning("Changeset validation reported %d error(s).", len(validate_errors))
    """

    store: Optional[ChangeSetStatusStore] = None
    if status_dir is not None:
        store = ChangeSetStatusStore(
            status_dir=status_dir,
            execution_id=execution_id,
            changeset_id=changeset._changeset_id,
        )
        store.start(total_operations=len(execution_changes))
        changeset.last_execution_id = store.execution_id
        logger.info(
            "changeset execution_id=%s status_file=%s", store.execution_id, store.path
        )

    ok_all = True
    folded_writer_indexes = set(rule_fold.result_from_index.values())
    folded_results: dict[int, Response] = {}

    for i, change in enumerate(execution_changes, start=1):
        obj = change.body
        action = ChangeType.from_raw(change.change_type)
        action_name = action.value
        obj_type = change.object_type.value
        obj_path = change.uri
        obj_name = getattr(obj, "name", "")
        progress_path = obj_path or (f"{obj_type}:{obj_name}" if obj_name else obj_type)
        progress_message = f"{action_name} {obj_type.lower()}"

        progress.on_event(
            ProgressEvent.make(
                kind=ProgressKind.START,
                scope=ProgressScope.WORKER,
                unit=ProgressUnit.LINE,
                current=0,
                total=1,
                message=progress_message,
                path=progress_path,
            )
        )

        if store is not None:
            store.begin_operation(i, action_name, obj_type, obj_name, change.uri)

        try:
            index = i - 1
            run = rule_fold.run_by_index.get(index, change)
            run_action = ChangeType.from_raw(run.change_type)
            run_obj_type = run.object_type.value
            if index in rule_fold.skipped_by_index:
                resp = _build_skipped_rule_response(change.uri, rule_fold.skipped_by_index[index])
            elif index in rule_fold.failed_by_index:
                raise ValueError(rule_fold.failed_by_index[index])
            elif index in rule_fold.result_from_index:
                # the cube's rules were written once, at an earlier row of the cube
                resp = folded_results[rule_fold.result_from_index[index]]
            elif run_action == ChangeType.ADD:
                resp = create_object(
                    tm1_service=tm1_service,
                    object_instance=run.body,
                    object_type=run_obj_type,
                    uri=run.uri,
                )
            elif run_action == ChangeType.MODIFY:
                resp = update_object(
                    tm1_service=tm1_service,
                    object_instance=run.body,
                    object_type=run_obj_type,
                    uri=run.uri,
                )
            elif run_action == ChangeType.REMOVE:
                resp = delete_object(
                    tm1_service=tm1_service,
                    object_instance=run.body,
                    object_type=run_obj_type,
                    uri=run.uri,
                )
            else:
                raise ValueError(f"Unknown action: {action_name}")

            resp = _normalize_apply_response(
                resp,
                action_name=action_name,
                obj_type=obj_type,
                obj_name=obj_name,
                obj_path=obj_path,
            )
            if index in folded_writer_indexes:
                folded_results[index] = resp
            changes.append(resp.url)

            logger.info(
                "operation %d/%d execution_id=%s %s %s%s -> %s %s",
                i,
                len(execution_changes),
                getattr(store, "execution_id", changeset.last_execution_id),
                action_name,
                f"{obj_type}:" if obj_name else obj_type,
                obj_name or "",
                resp.status_code,
                getattr(resp, "url", ""),
            )

            if store is not None:
                store.end_operation_with_response(resp)

            progress.on_event(
                ProgressEvent.make(
                    kind=ProgressKind.COMPLETE,
                    scope=ProgressScope.WORKER,
                    unit=ProgressUnit.LINE,
                    current=1,
                    total=1,
                    message=progress_message,
                    path=progress_path,
                )
            )
            progress.on_event(
                ProgressEvent.make(
                    kind=ProgressKind.UPDATE,
                    scope=ProgressScope.TOTAL,
                    unit=ProgressUnit.LINE,
                    current=i,
                    total=total_operations,
                    message="applying changeset",
                    path=changeset._changeset_id,
                )
            )

            if not resp.ok:
                ok_all = False
                if fail_fast:
                    if store is not None:
                        store.fail()
                    progress.on_event(
                        ProgressEvent.make(
                            kind=ProgressKind.COMPLETE,
                            scope=ProgressScope.TOTAL,
                            unit=ProgressUnit.LINE,
                            current=i,
                            total=total_operations,
                            message="apply stopped on failure",
                            path=changeset._changeset_id,
                        )
                    )
                    return False, changes

        except Exception as exc:
            logger.exception(
                "Exception during operation %d/%d execution_id=%s %s %s%s path=%s: %s",
                i,
                len(execution_changes),
                getattr(store, "execution_id", changeset.last_execution_id),
                action_name,
                f"{obj_type}:" if obj_name else obj_type,
                obj_name or "",
                obj_path,
                exc,
            )
            if store is not None:
                store.end_operation_with_exception(exc)
                store.fail()
            progress.on_event(
                ProgressEvent.make(
                    kind=ProgressKind.COMPLETE,
                    scope=ProgressScope.WORKER,
                    unit=ProgressUnit.LINE,
                    current=1,
                    total=1,
                    message=f"{progress_message} failed",
                    path=progress_path,
                )
            )
            progress.on_event(
                ProgressEvent.make(
                    kind=ProgressKind.COMPLETE,
                    scope=ProgressScope.TOTAL,
                    unit=ProgressUnit.LINE,
                    current=i,
                    total=total_operations,
                    message="apply failed",
                    path=changeset._changeset_id,
                )
            )
            logger.info(
                "Apply finished success=%s applied=%d attempted=%d",
                False,
                len(changes),
                i,
            )
            return False, changes

    if store is not None:
        store.succeed() if ok_all else store.fail()

    progress.on_event(
        ProgressEvent.make(
            kind=ProgressKind.COMPLETE,
            scope=ProgressScope.TOTAL,
            unit=ProgressUnit.LINE,
            current=len(execution_changes),
            total=total_operations,
            message="apply complete",
            path=changeset._changeset_id,
        )
    )

    logger.info(
        "Apply finished success=%s applied=%d attempted=%d execution_id=%s",
        ok_all,
        len(changes),
        len(execution_changes),
        getattr(store, "execution_id", changeset.last_execution_id),
    )
    return ok_all, changes


# --------------------------------------------------------------------------------
# CRUD operations for apply changeset function
# --------------------------------------------------------------------------------

def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _resolve_handler(module, action: str, object_type: str):
    candidates = [
        f"{action}_{object_type.lower()}",
        f"{action}_{_camel_to_snake(object_type)}",
    ]
    logger.debug(
        "Resolving handler action=%s object_type=%s module=%s candidates=%s",
        action,
        object_type,
        module.__name__,
        candidates,
    )
    for candidate in candidates:
        fn = getattr(module, candidate, None)
        if fn is not None:
            logger.debug(
                "Resolved handler '%s' in module '%s'", candidate, module.__name__
            )
            return fn
    raise AttributeError(
        f"No handler found for action='{action}', object_type='{object_type}' "
        f"in module '{module.__name__}'. Tried: {candidates}"
    )


def create_object(
    tm1_service: TM1Service, object_instance: T, object_type, uri: Optional[str] = None
) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    create = _resolve_handler(module, "create", object_type)
    object_name = getattr(object_instance, "name", "")
    try:
        try:
            return create(tm1_service, object_instance, uri=uri)
        except TypeError:
            return create(tm1_service, object_instance)
    except Exception as exc:
        if _is_technical_object_uri(
            uri, object_type, object_name
        ) and _is_duplicate_create_exception(exc):
            logger.warning(
                "Ignoring duplicate create failure for technical object %s%s path=%s: %s",
                f"{object_type}:" if object_name else object_type,
                object_name or "",
                uri,
                exc,
            )
            return _build_ignored_duplicate_create_response(
                object_type=object_type,
                object_name=object_name,
                uri=uri,
            )
        raise


def delete_object(
    tm1_service: TM1Service, object_instance: T, object_type, uri: Optional[str] = None
) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    delete = _resolve_handler(module, "delete", object_type)
    try:
        return delete(tm1_service, object_instance, uri=uri)
    except TypeError:
        return delete(tm1_service, object_instance)


def update_object(
    tm1_service: TM1Service, object_instance: T, object_type, uri: Optional[str] = None
) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    update = _resolve_handler(module, "update", object_type)
    try:
        return update(tm1_service, object_instance, uri=uri)
    except TypeError:
        return update(tm1_service, object_instance)


def _prepare_execution_changes(changes: Iterable[Change]) -> tuple[list[Change], _RuleFold]:
    incoming = list(changes)
    executable_changes = [
        change for change in incoming if getattr(change, "apply", True)
    ]
    skipped_count = len(incoming) - len(executable_changes)
    logger.debug(
        "Preparing execution changes from %d incoming change(s); skipped apply=false=%d",
        len(incoming),
        skipped_count,
    )
    execution_changes = executable_changes
    temp = Changeset()
    temp.changes = execution_changes
    sorted_execution_changes = list(temp.changes)
    deselected_cube_adds = {
        change.body.name
        for change in incoming
        if not getattr(change, "apply", True)
        and change.object_type == ObjectType.CUBE
        and change.change_type == ChangeType.ADD
    }
    rule_fold = _fold_rule_changes(sorted_execution_changes, deselected_cube_adds)
    logger.debug(
        "Prepared execution changes count=%d (from executable=%d)",
        len(sorted_execution_changes),
        len(executable_changes),
    )
    return sorted_execution_changes, rule_fold


# --------------------------------------------------------------------------------
# deployment hooks for running pre/post pull/push tasks in apply workflow
# --------------------------------------------------------------------------------

def _tm1project_reference_name(value: object, prefix: str) -> Optional[str]:
    if not isinstance(value, str):
        return None

    match = re.match(
        rf"^\s*{re.escape(prefix)}\('(.+)'\)\s*$", value, flags=re.IGNORECASE
    )
    if match:
        return match.group(1)

    stripped = value.strip()
    return stripped or None


def _ti_string_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _build_unified_deployment_hook_process(
    process_specs: list[dict[str, object]],
    process_name: str,
) -> TM1pyProcess:
    prolog = "\n".join(
        _build_execute_process_statement(
            process_name=spec["process_name"],
            parameters=spec.get("parameters"),
        )
        for spec in process_specs
    )
    return TM1pyProcess(
        name=process_name,
        has_security_access=False,
        prolog_procedure=prolog,
        metadata_procedure="",
        data_procedure="",
        epilog_procedure="",
        datasource_type="None",
    )


def _build_execute_process_statement(process_name: object, parameters: object) -> str:
    args = [_ti_string_literal(process_name)]
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            param_name = parameter.get("Name") or parameter.get("name")
            if not param_name:
                continue
            param_value = (
                parameter.get("Value")
                if "Value" in parameter
                else parameter.get("value", "")
            )
            args.append(_ti_string_literal(param_name))
            args.append(_ti_string_literal("" if param_value is None else param_value))
    return f"ExecuteProcess({', '.join(args)});"


def _parse_tasks_from_project_file(
    task_refs: list,
    tasks: dict,
    operation: Literal["PrePull", "PostPull"],
) -> list[dict[str, object]]:
    process_specs: list[dict[str, object]] = []
    for task_ref in task_refs:
        task_name = _tm1project_reference_name(task_ref, "Tasks")
        if not task_name:
            raise ValueError(f"Invalid {operation} task reference: {task_ref!r}")

        task_def = tasks.get(task_name)
        if not isinstance(task_def, dict):
            raise ValueError(f"Task '{task_name}' not found in project Tasks")

        process_ref = task_def.get("Process") or task_def.get("process")
        process_name = _tm1project_reference_name(process_ref, "Processes")
        if not process_name:
            raise ValueError(f"Task '{task_name}' missing Process reference")

        process_specs.append(
            {
                "process_name": process_name,
                "parameters": task_def.get("Parameters")
                or task_def.get("parameters")
                or [],
            }
        )
    return process_specs


def _load_tm1project_json(project_file_path: Path | str) -> tuple[Path, dict]:
    project_path = Path(project_file_path).expanduser()
    with open(project_path, encoding="utf-8") as handle:
        project_file = json.load(handle)

    if not isinstance(project_file, dict):
        raise ValueError(f"tm1project file must contain a JSON object: {project_path}")

    return project_path, project_file


def _resolve_operation_process_specs(
    project_json: dict,
    environment: str,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
    project_path: Optional[Path] = None,
) -> list[dict[str, object]]:
    deployment = project_json.get("Deployment") or {}
    if not isinstance(deployment, dict):
        raise ValueError("Deployment must be a JSON object when present")

    environment_config = deployment.get(environment) or {}
    if not isinstance(environment_config, dict):
        raise ValueError(
            f"Deployment environment '{environment}' must be a JSON object"
        )

    task_refs = environment_config.get(operation) or []
    if not task_refs:
        logger.info(
            "No %s tasks for environment=%s project=%s.",
            operation,
            environment,
            project_path,
        )
        return []

    tasks = project_json.get("Tasks") or {}
    if not isinstance(tasks, dict):
        raise ValueError("Tasks must be a JSON object when present")

    process_specs = _parse_tasks_from_project_file(
        task_refs=task_refs,
        tasks=tasks,
        operation=operation,
    )
    if not process_specs:
        logger.info(
            "No executable %s tasks for environment=%s project=%s.",
            operation,
            environment,
            project_path,
        )
    return process_specs


def _cleanup_temp_process(
    tm1_service: TM1Service,
    process_name: str,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
) -> None:
    try:
        tm1_service.processes.delete(process_name)
    except Exception:
        logger.exception("Failed to delete temp %s process %s", operation, process_name)


def _create_and_run_temp_process(
    tm1_service: TM1Service,
    process_specs: list[dict[str, object]],
    environment: str,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
    **kwargs,
) -> Optional[Response]:
    if not process_specs:
        return None

    unified_process_name = (
        f"tm1_git_py_{str(operation).lower()}_{environment}_{uuid.uuid4().hex}"
    )
    unified_process = _build_unified_deployment_hook_process(
        process_specs, unified_process_name
    )

    tm1_service.processes.create(unified_process)
    try:
        return tm1_service.processes.execute(
            process_name=unified_process_name,
            parameters={},
            **kwargs,
        )
    finally:
        _cleanup_temp_process(tm1_service, unified_process_name, operation)


def _get_deployment_hook_operations(
    project_file_path: Path | str,
    environment: str,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
) -> list[dict[str, object]]:
    project_path, project_json = _load_tm1project_json(project_file_path)

    return _resolve_operation_process_specs(
        project_json=project_json,
        environment=environment,
        operation=operation,
        project_path=project_path,
    )


def _apply_deployment_hook_operations(
    tm1_service: TM1Service,
    environment: str,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
    project_file_path: Optional[Path | str] = None,
    project_json: Optional[dict] = None,
    **kwargs
) -> Optional[Response]:
    if project_json is None and project_file_path is None:
        raise ValueError(
            "Pass either project_file_path for a valid tm1project.json file "
            "or the JSON body in process_specs."
        )
    if project_json and project_file_path:
        raise ValueError(
            "Pass either project_file_path for a valid tm1project.json file "
            "or the JSON body in process_specs, but NOT both."
        )

    if project_file_path is not None:
        process_specs = _get_deployment_hook_operations(
            project_file_path=project_file_path,
            environment=environment,
            operation=operation,
        )
    else:
        if not isinstance(project_json, dict):
            raise ValueError("tm1project JSON body must be a JSON object")

        process_specs = _resolve_operation_process_specs(
            project_json=project_json,
            environment=environment,
            operation=operation
        )

    return _create_and_run_temp_process(
        tm1_service=tm1_service,
        process_specs=process_specs,
        environment=environment,
        operation=operation,
        **kwargs
    )


apply_pre_pull_operations = partial(
    _apply_deployment_hook_operations, operation="PrePull"
)
apply_post_pull_operations = partial(
    _apply_deployment_hook_operations, operation="PostPull"
)
apply_pre_push_operations = partial(
    _apply_deployment_hook_operations, operation="PrePush"
)
apply_post_push_operations = partial(
    _apply_deployment_hook_operations, operation="PostPush"
)


get_pre_pull_operations = partial(
    _get_deployment_hook_operations, operation="PrePull"
)
get_post_pull_operations = partial(
    _get_deployment_hook_operations, operation="PostPull"
)
get_pre_push_operations = partial(
    _get_deployment_hook_operations, operation="PrePush"
)
get_post_push_operations = partial(
    _get_deployment_hook_operations, operation="PostPush"
)
