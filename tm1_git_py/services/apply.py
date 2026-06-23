import importlib
import logging
import re
import uuid
from pathlib import Path
from typing import Iterable, Optional, Union, TypeVar

import TM1py
from TM1py import TM1Service
from requests import Response

from tm1_git_py import Changeset
from tm1_git_py.model import Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore, Element, Edge, Rule
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
from tm1_git_py.services.filter import DEFAULT_TM1_TECHNICAL_OBJECTS, should_exclude_path

logger = logging.getLogger(__name__)

T = TypeVar('T', Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore, Element, Edge, Rule)


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


def _is_technical_object_uri(uri: Optional[str], object_type: str, object_name: str) -> bool:
    if uri:
        try:
            return should_exclude_path(uri, DEFAULT_TM1_TECHNICAL_OBJECTS)
        except Exception:
            logger.debug("Failed to classify technical object from uri='%s'", uri, exc_info=True)
    return object_type in {"Cube", "Dimension", "Process"} and (object_name or "").startswith("}")


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
        store = ChangeSetStatusStore(status_dir=status_dir, execution_id=execution_id,
                                     changeset_id=changeset._changeset_id)
        store.start(total_operations=len(execution_changes))
        changeset.last_execution_id = store.execution_id
        logger.info("changeset execution_id=%s status_file=%s", store.execution_id, store.path)

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

            logger.info("operation %d/%d execution_id=%s %s %s%s -> %s %s",
                        i,
                        len(execution_changes),
                        getattr(store, "execution_id", changeset.last_execution_id),
                        action_name,
                        f"{obj_type}:" if obj_name else obj_type,
                        obj_name or "",
                        resp.status_code,
                        getattr(resp, "url", ""))

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
            logger.exception("Exception during operation %d/%d execution_id=%s %s %s%s path=%s: %s",
                             i,
                             len(execution_changes),
                             getattr(store, "execution_id", changeset.last_execution_id),
                             action_name,
                             f"{obj_type}:" if obj_name else obj_type,
                             obj_name or "",
                             obj_path,
                             exc)
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
            logger.info("Apply finished success=%s applied=%d attempted=%d", False, len(changes), i)
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
            logger.debug("Resolved handler '%s' in module '%s'", candidate, module.__name__)
            return fn
    raise AttributeError(
        f"No handler found for action='{action}', object_type='{object_type}' "
        f"in module '{module.__name__}'. Tried: {candidates}"
    )


def create_object(tm1_service: TM1Service, object_instance: T, object_type, uri: Optional[str] = None) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    create = _resolve_handler(module, "create", object_type)
    object_name = getattr(object_instance, "name", "")
    try:
        try:
            return create(tm1_service, object_instance, uri=uri)
        except TypeError:
            return create(tm1_service, object_instance)
    except Exception as exc:
        if _is_technical_object_uri(uri, object_type, object_name) and _is_duplicate_create_exception(exc):
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


def delete_object(tm1_service: TM1Service, object_instance: T, object_type, uri: Optional[str] = None) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    delete = _resolve_handler(module, "delete", object_type)
    try:
        return delete(tm1_service, object_instance, uri=uri)
    except TypeError:
        return delete(tm1_service, object_instance)


def update_object(tm1_service: TM1Service, object_instance: T, object_type, uri: Optional[str] = None) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    update = _resolve_handler(module, "update", object_type)
    try:
        return update(tm1_service, object_instance, uri=uri)
    except TypeError:
        return update(tm1_service, object_instance)


def _prepare_execution_changes(changes: Iterable[Change]) -> list[Change]:
    incoming = list(changes)
    executable_changes = [change for change in incoming if getattr(change, "apply", True)]
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
# Master TI for batch update and rollback functionality
# --------------------------------------------------------------------------------

ATOMIC_SCHEMA_OBJECT_TYPES = {
    ObjectType.DIMENSION,
    ObjectType.HIERARCHY,
    ObjectType.ELEMENT,
    ObjectType.EDGE,
    ObjectType.SUBSET,
    ObjectType.CUBE,
    ObjectType.MDX_VIEW,
    ObjectType.NATIVE_VIEW,
    ObjectType.RULE,
}

PROCESS_AND_CHORE_OBJECT_TYPES = {
    ObjectType.PROCESS,
    ObjectType.CHORE,
}


def _filter_changeset(changeset: Changeset, object_types: set[ObjectType]) -> Changeset:
    filtered = Changeset()
    filtered._changeset_id = changeset._changeset_id
    filtered.last_execution_id = changeset.last_execution_id
    filtered.errors = dict(changeset.errors)
    filtered.changes = [
        change for change in changeset.changes
        if change.object_type in object_types
    ]
    return filtered


def build_master_changeset_ti(changeset: Changeset) -> str:
    """
    Compiles a Changeset object into a single Atomic TurboIntegrator script.
    """
    ti_lines: list[str] = [
        "# **** Atomic Changeset Execution ****",
        "",
    ]

    # Keep execution order aligned with the regular apply pipeline.
    execution_changes = _prepare_execution_changes(changeset.changes)
    action_to_suffix = {
        ChangeType.ADD: "create",
        ChangeType.MODIFY: "update",
        ChangeType.REMOVE: "delete",
    }

    for change in execution_changes:
        action = ChangeType.from_raw(change.change_type)
        builder_suffix = action_to_suffix[action]
        obj = change.body
        module = importlib.import_module(obj.__class__.__module__)
        object_type = change.object_type.value

        candidates = [
            object_type.lower(),
            _camel_to_snake(object_type),
            obj.__class__.__name__.lower(),
            _camel_to_snake(obj.__class__.__name__),
        ]

        builder = None
        for candidate in dict.fromkeys(candidates):
            fn = getattr(module, f"build_{candidate}_{builder_suffix}_ti", None)
            if fn is not None:
                builder = fn
                break

        if builder is None:
            logger.debug(
                "Skipping TI snippet build for %s %s: no build_*_%s_ti function in %s",
                action.value,
                object_type,
                builder_suffix,
                module.__name__,
            )
            continue

        try:
            snippet = builder(obj, uri=change.uri)
        except TypeError:
            snippet = builder(obj)
        if snippet:
            ti_lines.append(snippet)
            ti_lines.append("")

    return "\r\n".join(ti_lines)


def apply_atomic(changeset: Changeset, tm1_service: TM1Service) -> bool:
    if not changeset.has_changes():
        return True

    # 1. Generate the Code
    master_ti_code = build_master_changeset_ti(changeset)
    print(master_ti_code)

    # 2. Create Ephemeral Process
    process_name = f"}}git_atomic_{uuid.uuid4().hex}"
    process = TM1py.Process(
        name=process_name,
        prolog_procedure=master_ti_code,
        has_security_access=True
    )

    try:
        # 3. Deploy
        tm1_service.processes.create(process)

        # 4. Execute (The Atomic Moment)
        tm1_service.processes.execute(process_name)
        return True

    except Exception as e:
        print(f"Atomic Batch Failed: {e}")
        raise e

    finally:
        # 5. Cleanup
        if tm1_service.processes.exists(process_name):
            tm1_service.processes.delete(process_name)


def apply_with_atomic_schema(
        changeset: Changeset,
        tm1_service: TM1Service,
        *,
        status_dir: Optional[Union[str, Path]] = None,
        execution_id: Optional[str] = None,
        fail_fast: bool = True,
        progress_sink: Optional[ProgressSink] = None,
) -> tuple[bool, Union[list, None]]:
    """
    Apply schema changes atomically, then apply process and chore changes via the regular TM1py flow.
    """
    logger.info(
        "Starting atomic-schema apply execution_id=%s fail_fast=%s changes=%d",
        execution_id,
        fail_fast,
        len(changeset.changes),
    )
    if not changeset.has_changes():
        logger.info("No changes to apply.")
        return True, None

    schema_changeset = _filter_changeset(changeset, ATOMIC_SCHEMA_OBJECT_TYPES)
    process_and_chore_changeset = _filter_changeset(changeset, PROCESS_AND_CHORE_OBJECT_TYPES)

    applied_changes: list[str] = []

    if schema_changeset.has_changes():
        logger.info("Applying %d schema change(s) atomically", len(schema_changeset.changes))
        ok = apply_atomic(schema_changeset, tm1_service)
        if not ok:
            return False, None
        applied_changes.extend(
            change.uri for change in _prepare_execution_changes(schema_changeset.changes)
        )

    if process_and_chore_changeset.has_changes():
        logger.info(
            "Applying %d process/chore change(s) through regular TM1py apply",
            len(process_and_chore_changeset.changes),
        )
        ok, changes = apply(
            changeset=process_and_chore_changeset,
            tm1_service=tm1_service,
            status_dir=status_dir,
            execution_id=execution_id,
            fail_fast=fail_fast,
            progress_sink=progress_sink,
        )
        if changes:
            applied_changes.extend(changes)
        return ok, applied_changes or None

    return True, applied_changes or None
