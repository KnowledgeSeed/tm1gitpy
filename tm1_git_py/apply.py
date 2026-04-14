import importlib
import logging
import re
from pathlib import Path
from typing import Iterable, Optional, Union, TypeVar

from TM1py import TM1Service
from requests import Response

from tm1_git_py import Changeset
from tm1_git_py.changeset import ChangeType, Change, ObjectType
from tm1_git_py.changeset_status import ChangeSetStatusStore
from tm1_git_py.model import Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore, Element, Edge, Rule
from tm1_git_py.validation import validate_changeset

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

def apply(
        changeset: Changeset,
        tm1_service: TM1Service,
        *,
        status_dir: Optional[Union[str, Path]] = None,
        execution_id: Optional[str] = None,
        fail_fast: bool = True
) -> tuple[bool, Union[list, None]]:

    changes = []
    logger.info(
        "Starting apply changeset_id=%s fail_fast=%s changes=%d",
        changeset.changeset_id,
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
                                     changeset_id=changeset.changeset_id)
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

            if not resp.ok:
                ok_all = False
                if fail_fast:
                    if store is not None:
                        store.fail()
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
            logger.info("Apply finished success=%s applied=%d attempted=%d", False, len(changes), i)
            return False, changes

    if store is not None:
        store.succeed() if ok_all else store.fail()

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
    try:
        return create(tm1_service, object_instance, uri=uri)
    except TypeError:
        return create(tm1_service, object_instance)


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
    non_rule_changes: list[Change] = []
    rule_changes_by_cube: dict[str, Change] = {}

    for change in executable_changes:
        if change.object_type == ObjectType.RULE:
            cube_name = Rule.cube_name_from_uri(change.uri)
            if not cube_name:
                raise ValueError(f"Invalid rule change uri: '{change.uri}'")
            # Keep the last rule change for a cube; compare path now emits one unified entry.
            rule_changes_by_cube[cube_name] = change
        else:
            non_rule_changes.append(change)

    synthesized_cube_changes: list[Change] = []
    for cube_name, rule_change in rule_changes_by_cube.items():
        action = ChangeType.from_raw(rule_change.change_type)
        if action == ChangeType.REMOVE:
            cube_rules: list[Rule] = []
        else:
            cube_rules = [rule_change.body] if isinstance(rule_change.body, Rule) else []

        synthesized_cube_changes.append(
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.CUBE,
                uri=Cube.uri_for(cube_name),
                body=Cube(
                    name=cube_name,
                    dimensions=[],
                    rules=cube_rules,
                    views=[],
                ),
            )
        )
    logger.debug(
        "Synthesized cube updates from rule changes cubes=%d",
        len(synthesized_cube_changes),
    )

    execution_changes = non_rule_changes + synthesized_cube_changes
    temp = Changeset()
    temp.changes = execution_changes
    sorted_execution_changes = list(temp.changes)
    logger.debug(
        "Prepared execution changes count=%d (from executable=%d)",
        len(sorted_execution_changes),
        len(executable_changes),
    )
    return sorted_execution_changes
