import importlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from decimal import Decimal
from typing import Iterable, Mapping, Optional, Union, TypeVar, Literal

from TM1py import TM1Service, Process as TM1pyProcess
from TM1py.Exceptions import TM1pyRestException
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
from tm1_git_py.reporting.progress_reporting import (
    NoopProgressSink,
    ProgressEvent,
    ProgressKind,
    ProgressScope,
    ProgressSink,
    ProgressUnit,
)
from tm1_git_py.services.changeset import ChangeType, Change
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

    execution_changes = _prepare_execution_changes(changeset.changes)
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
            if action == ChangeType.ADD:
                resp = create_object(
                    tm1_service=tm1_service,
                    object_instance=obj,
                    object_type=obj_type,
                    uri=change.uri,
                )
            elif action == ChangeType.MODIFY:
                resp = update_object(
                    tm1_service=tm1_service,
                    object_instance=obj,
                    object_type=obj_type,
                    uri=change.uri,
                )
            elif action == ChangeType.REMOVE:
                resp = delete_object(
                    tm1_service=tm1_service,
                    object_instance=obj,
                    object_type=obj_type,
                    uri=change.uri,
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


def _prepare_execution_changes(changes: Iterable[Change]) -> list[Change]:
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
    logger.debug(
        "Prepared execution changes count=%d (from executable=%d)",
        len(sorted_execution_changes),
        len(executable_changes),
    )
    return sorted_execution_changes


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


def _ti_numeric_literal(value: object) -> str:
    if isinstance(value, bool):
        raise ValueError("not a number")
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("not a finite number")
    if number.is_integer():
        return str(int(number))
    # Plain decimal notation: repr() switches to an exponent for small values.
    return format(Decimal(repr(number)), "f")


def _build_execute_process_statement(
    process_name: object,
    parameters: Iterable[tuple[str, object, str]],
) -> str:
    """Build one ``ExecuteProcess`` call from ``(name, value, declared type)`` triples."""
    args = [_ti_string_literal(process_name)]
    for param_name, param_value, param_type in parameters or ():
        args.append(_ti_string_literal(param_name))
        if param_type == "Numeric":
            args.append(_ti_numeric_literal(param_value))
        else:
            args.append(_ti_string_literal(param_value))
    return f"ExecuteProcess({', '.join(args)});"


def _normalise_task_parameters(task_name: object, parameters: object) -> dict[str, object]:
    """Return a task's parameters as an ordered ``{name: value}`` mapping.

    Accepts the tm1project.json list form (``Name``/``Value`` or
    ``name``/``value`` entries) and an already normalised mapping.
    """
    if not parameters:
        return {}
    if isinstance(parameters, Mapping):
        entries = list(parameters.items())
    elif isinstance(parameters, list):
        entries = []
        for parameter in parameters:
            if not isinstance(parameter, dict):
                raise ValueError(
                    f"Task '{task_name}': each Parameters entry must be an object "
                    "with Name and Value"
                )
            param_name = parameter.get("Name") or parameter.get("name")
            if not param_name:
                raise ValueError(
                    f"Task '{task_name}': a Parameters entry has no Name"
                )
            param_value = (
                parameter.get("Value")
                if "Value" in parameter
                else parameter.get("value", "")
            )
            entries.append((param_name, param_value))
    else:
        raise ValueError(f"Task '{task_name}': Parameters must be a list")

    normalised: dict[str, object] = {}
    seen: set[str] = set()
    for param_name, param_value in entries:
        param_name = str(param_name)
        # TI variable names are case-insensitive.
        if param_name.casefold() in seen:
            raise ValueError(
                f"Task '{task_name}': parameter '{param_name}' is given more than once"
            )
        seen.add(param_name.casefold())
        if param_value is None:
            param_value = ""
        if isinstance(param_value, bool) or not isinstance(
            param_value, (str, int, float)
        ):
            raise ValueError(
                f"Task '{task_name}': value of parameter '{param_name}' must be "
                "a string or a number"
            )
        normalised[param_name] = param_value
    return normalised


def _merge_parameter_overrides(
    process_specs: list[dict[str, object]],
    parameter_overrides: Optional[Mapping[str, Mapping[str, object]]] = None,
) -> list[dict[str, object]]:
    """Normalise each spec's parameters and apply run-time overrides on top.

    Overrides are keyed by task name. An override replaces the tm1project.json
    value of the same parameter (names match case-insensitively) or is added
    after the project file's parameters.
    """
    parameter_overrides = parameter_overrides or {}
    known_tasks = {spec.get("task_name") for spec in process_specs}
    unknown_tasks = [task for task in parameter_overrides if task not in known_tasks]
    if unknown_tasks:
        raise ValueError(
            "Parameter overrides name task(s) that are not part of this hook: "
            + ", ".join(repr(str(task)) for task in unknown_tasks)
        )

    merged_specs: list[dict[str, object]] = []
    for spec in process_specs:
        task_name = spec.get("task_name")
        parameters = _normalise_task_parameters(task_name, spec.get("parameters"))
        overrides = _normalise_task_parameters(
            task_name, parameter_overrides.get(task_name)
        )
        file_names = {name.casefold(): name for name in parameters}
        for override_name, override_value in overrides.items():
            file_name = file_names.get(override_name.casefold())
            if file_name is None:
                parameters[override_name] = override_value
                continue
            # Names only: parameter values can be credentials.
            logger.info(
                "Task '%s': run-time value of parameter '%s' replaces the "
                "tm1project.json value.",
                task_name,
                file_name,
            )
            parameters[file_name] = override_value
        merged_specs.append({**spec, "parameters": parameters})
    return merged_specs


def _resolve_declared_parameters(
    tm1_service: TM1Service,
    process_specs: list[dict[str, object]],
) -> dict[str, dict[str, tuple[str, str]]]:
    """Read the declared parameters of every process the specs reference.

    Returns ``{process_name: {casefolded name: (declared name, type)}}``.
    """
    declared: dict[str, dict[str, tuple[str, str]]] = {}
    for spec in process_specs:
        process_name = str(spec["process_name"])
        if process_name in declared:
            continue
        try:
            process = tm1_service.processes.get(process_name)
        except TM1pyRestException as exc:
            if exc.status_code == 404:
                raise ValueError(
                    f"Task '{spec.get('task_name', process_name)}' references "
                    f"process '{process_name}', which does not exist on the server"
                ) from exc
            raise
        declared[process_name] = {}
        for parameter in process.parameters:
            param_name = str(parameter["Name"])
            # Type was introduced with TM1 11; older servers only give the value.
            param_type = parameter.get("Type") or (
                "String" if isinstance(parameter.get("Value"), str) else "Numeric"
            )
            declared[process_name][param_name.casefold()] = (param_name, param_type)
    return declared


def _apply_declared_parameter_types(
    process_specs: list[dict[str, object]],
    declared_parameters: dict[str, dict[str, tuple[str, str]]],
) -> list[dict[str, object]]:
    """Return specs whose parameters are ``(declared name, value, type)`` triples.

    Raises ``ValueError`` for a parameter the process does not declare and for
    a value that does not fit the declared type.
    """
    typed_specs: list[dict[str, object]] = []
    for spec in process_specs:
        process_name = str(spec["process_name"])
        task_name = spec.get("task_name", process_name)
        declared = declared_parameters[process_name]
        typed_parameters: list[tuple[str, object, str]] = []
        for param_name, param_value in _normalise_task_parameters(
            task_name, spec.get("parameters")
        ).items():
            if param_name.casefold() not in declared:
                raise ValueError(
                    f"Task '{task_name}': process '{process_name}' has no "
                    f"parameter '{param_name}'"
                )
            declared_name, param_type = declared[param_name.casefold()]
            if param_type == "Numeric":
                try:
                    _ti_numeric_literal(param_value)
                except ValueError:
                    # The value is left out: it can be a credential.
                    raise ValueError(
                        f"Task '{task_name}': parameter '{declared_name}' of "
                        f"process '{process_name}' is Numeric, but the given "
                        "value is not a number"
                    ) from None
            typed_parameters.append((declared_name, param_value, param_type))
        typed_specs.append({**spec, "parameters": typed_parameters})
    return typed_specs


def _parse_tasks_from_project_file(
    task_refs: list,
    tasks: dict,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
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
                "task_name": task_name,
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
    parameter_overrides: Optional[Mapping[str, Mapping[str, object]]] = None,
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
        return _merge_parameter_overrides([], parameter_overrides)

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
    return _merge_parameter_overrides(process_specs, parameter_overrides)


def _cleanup_temp_process(
    tm1_service: TM1Service,
    process_name: str,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
) -> None:
    try:
        tm1_service.processes.delete(process_name)
    except Exception:
        logger.exception("Failed to delete temp %s process %s", operation, process_name)


class DeploymentHookError(RuntimeError):
    """A hook phase ran, and the TI code of at least one task logged an error.

    ``failures`` holds one mapping per ``ERROR`` entry of the run with the
    keys ``task_name``, ``process_name``, ``message``, ``logger`` and
    ``timestamp``. ``response`` is the response of the wrapper execution.
    """

    def __init__(
        self,
        operation: str,
        environment: str,
        failures: list[dict[str, object]],
        response: Optional[Response] = None,
    ):
        self.operation = operation
        self.environment = environment
        self.failures = failures
        self.response = response
        super().__init__(
            f"{operation} tasks for environment '{environment}' logged "
            f"{len(failures)} error(s): "
            + "; ".join(_format_hook_failure(failure) for failure in failures)
        )


def _format_hook_failure(failure: Mapping[str, object]) -> str:
    if failure.get("task_name") is None:
        return str(failure.get("message"))
    return (
        f"task '{failure['task_name']}' (process '{failure['process_name']}'): "
        f"{failure.get('message')}"
    )


# The server writes the wrapper's closing line shortly after the execute call
# returns, so the log is read again a few times until that line is there.
_HOOK_LOG_READ_ATTEMPTS = 5
_HOOK_LOG_READ_INTERVAL_SECONDS = 0.5
_HOOK_LOG_CLOCK_MARGIN = timedelta(seconds=60)
_HOOK_TASK_START_PATTERN = re.compile(
    r'^Process "(?P<process>.*)" run from process "(?P<parent>.*)" by user '
)
_PROCESS_QUIT_MESSAGE = "Execution was aborted by ProcessQuit Function"


def _hook_log_start_time(tm1_service: TM1Service) -> datetime:
    """Return a server-side time that lies before the hook run.

    The timestamp of the newest log entry is on the server's clock, so the
    client clock only matters when the log is empty or unreadable.
    """
    fallback = datetime.now(timezone.utc) - _HOOK_LOG_CLOCK_MARGIN
    try:
        entries = tm1_service.server.get_message_log_entries(reverse=True, top=1)
        timestamp = str(entries[0]["TimeStamp"])
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except Exception:
        logger.debug("No usable message log entry before the hook run", exc_info=True)
        return fallback


def _select_hook_run_entries(
    entries: Iterable[Mapping[str, object]],
    wrapper_process_name: str,
) -> tuple[Optional[list[Mapping[str, object]]], bool]:
    """Return the log entries of one wrapper run and whether the run is closed.

    A run is one block on one thread, from the wrapper's "executed by user"
    line to its "finished executing" line. The thread alone does not identify
    a run, because the server reuses it. The entries are ``None`` when the
    opening line is not in the log.
    """
    start_prefix = f'Process "{wrapper_process_name}" executed by user'
    finish_prefix = f'Process "{wrapper_process_name}":'
    run_entries: Optional[list[Mapping[str, object]]] = None
    thread_id: object = None

    for entry in entries:
        message = str(entry.get("Message", ""))
        if run_entries is None:
            if message.startswith(start_prefix):
                run_entries = [entry]
                thread_id = entry.get("ThreadID")
            continue
        if entry.get("ThreadID") != thread_id:
            continue
        run_entries.append(entry)
        if message.startswith(finish_prefix) and "finished executing" in message:
            return run_entries, True

    return run_entries, False


def _find_hook_failures(
    run_entries: Iterable[Mapping[str, object]],
    wrapper_process_name: str,
    process_specs: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Return the ``ERROR`` entries of a hook run that count as a failure.

    A ``ProcessQuit`` is not a failure by itself, so the line the server writes
    for every quit is skipped, and so is the wrapper's own closing line. What
    remains is what the TI code logged as ``ERROR`` and real TI errors.
    """
    wrapper_finish_prefix = f'Process "{wrapper_process_name}":'
    failures: list[dict[str, object]] = []
    task_index = -1
    current_spec: Mapping[str, object] = {}

    for entry in run_entries:
        message = str(entry.get("Message", ""))
        task_start = _HOOK_TASK_START_PATTERN.match(message)
        # Only a process started by the wrapper is a task. A process that a
        # task starts itself stays attributed to that task.
        if task_start and task_start.group("parent") == wrapper_process_name:
            task_index += 1
            current_spec = (
                process_specs[task_index] if task_index < len(process_specs) else {}
            )
            if not current_spec:
                current_spec = {"process_name": task_start.group("process")}
            continue

        if str(entry.get("Level", "")).upper() != "ERROR":
            continue
        if _PROCESS_QUIT_MESSAGE in message:
            continue
        if message.startswith(wrapper_finish_prefix) and "finished executing" in message:
            continue

        failures.append(
            {
                "task_name": current_spec.get("task_name"),
                "process_name": current_spec.get("process_name"),
                "message": message,
                "logger": entry.get("Logger"),
                "timestamp": entry.get("TimeStamp"),
            }
        )

    return failures


def _collect_hook_failures(
    tm1_service: TM1Service,
    wrapper_process_name: str,
    process_specs: list[dict[str, object]],
    since: datetime,
) -> list[dict[str, object]]:
    """Read the server message log and return the failures of one hook run."""
    run_entries: Optional[list[Mapping[str, object]]] = None
    for attempt in range(_HOOK_LOG_READ_ATTEMPTS):
        if attempt:
            time.sleep(_HOOK_LOG_READ_INTERVAL_SECONDS)
        entries = tm1_service.server.get_message_log_entries(
            reverse=False, since=since
        )
        run_entries, finished = _select_hook_run_entries(entries, wrapper_process_name)
        if finished:
            break
    else:
        logger.warning(
            "The server message log holds no closing entry for process %s; "
            "the error check covers the entries found so far",
            wrapper_process_name,
        )

    if run_entries is None:
        logger.warning(
            "The server message log holds no entry for process %s; "
            "errors logged by its tasks cannot be detected",
            wrapper_process_name,
        )
        return []

    return _find_hook_failures(run_entries, wrapper_process_name, process_specs)


def _create_and_run_temp_process(
    tm1_service: TM1Service,
    process_specs: list[dict[str, object]],
    environment: str,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
    **kwargs,
) -> Optional[Response]:
    """Run the tasks through one temporary wrapper process.

    Raises ``DeploymentHookError`` when the TI code of a task logged an
    ``ERROR`` during the run. All tasks run before that is reported.
    """
    if not process_specs:
        return None

    unified_process_name = (
        f"tm1_git_py_{str(operation).lower()}_{environment}_{uuid.uuid4().hex}"
    )
    # Parameters are checked against the server before anything is created,
    # so a bad name or value never leaves a half-run hook behind.
    typed_process_specs = _apply_declared_parameter_types(
        process_specs,
        _resolve_declared_parameters(tm1_service, process_specs),
    )
    unified_process = _build_unified_deployment_hook_process(
        typed_process_specs, unified_process_name
    )

    log_start_time = _hook_log_start_time(tm1_service)
    failures: list[dict[str, object]] = []
    tm1_service.processes.create(unified_process)
    try:
        response = tm1_service.processes.execute(
            process_name=unified_process_name,
            parameters={},
            **kwargs,
        )
        try:
            failures = _collect_hook_failures(
                tm1_service, unified_process_name, process_specs, log_start_time
            )
        except Exception:
            # The tasks have run. A failed log read must not turn that into a
            # failed hook, but it must not pass silently either.
            logger.warning(
                "Could not read the server message log after the %s tasks; "
                "errors logged by the tasks were not checked",
                operation,
                exc_info=True,
            )
    finally:
        _cleanup_temp_process(tm1_service, unified_process_name, operation)

    if failures:
        raise DeploymentHookError(operation, environment, failures, response)
    return response


def _get_deployment_hook_operations(
    project_file_path: Path | str,
    environment: str,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
    parameter_overrides: Optional[Mapping[str, Mapping[str, object]]] = None,
) -> list[dict[str, object]]:
    project_path, project_json = _load_tm1project_json(project_file_path)

    return _resolve_operation_process_specs(
        project_json=project_json,
        environment=environment,
        operation=operation,
        project_path=project_path,
        parameter_overrides=parameter_overrides,
    )


def _apply_deployment_hook_operations(
    tm1_service: TM1Service,
    environment: str,
    operation: Literal["PrePull", "PostPull", "PrePush", "PostPush"],
    project_file_path: Optional[Path | str] = None,
    project_json: Optional[dict] = None,
    parameter_overrides: Optional[Mapping[str, Mapping[str, object]]] = None,
    **kwargs
) -> Optional[Response]:
    """Run the tasks configured for one hook phase of an environment.

    ``parameter_overrides`` maps a task name to ``{parameter name: value}`` and
    takes precedence over the task's ``Parameters`` in tm1project.json. It is
    an explicit keyword because ``kwargs`` is forwarded to ``processes.execute``.
    """
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
            parameter_overrides=parameter_overrides,
        )
    else:
        if not isinstance(project_json, dict):
            raise ValueError("tm1project JSON body must be a JSON object")

        process_specs = _resolve_operation_process_specs(
            project_json=project_json,
            environment=environment,
            operation=operation,
            parameter_overrides=parameter_overrides,
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
