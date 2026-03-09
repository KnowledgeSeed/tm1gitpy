import importlib
import logging
import uuid
import re
import TM1py
from pathlib import Path
from typing import Optional, Union, TypeVar

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
        changeset_name: Optional[str] = None,
        fail_fast: bool = True
) -> tuple[bool, Union[list, None]]:

    changes = []
    if not changeset.has_changes():
        logger.info("No changes to apply.")
        return True, None

    execution_changes = _prepare_execution_changes(changeset.changes)
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
                                     changeset_name=changeset_name)
        store.start(total_operations=len(execution_changes))
        changeset.last_execution_id = store.execution_id
        logger.info("changeset execution_id=%s status_file=%s", store.execution_id, store.path)

    ok_all = True

    for i, change in enumerate(execution_changes, start=1):
        obj = change.body
        action = ChangeType.from_raw(change.change_type)
        action_name = action.value
        obj_type = change.object_type.value
        obj_path = change.source_path
        obj_name = getattr(obj, "name", "")

        if store is not None:
            store.begin_operation(i, action_name, obj_type, obj_name, obj_path)

        try:
            if action == ChangeType.ADD:
                resp = create_object(tm1_service=tm1_service, object_instance=obj, object_type=obj_type)
            elif action == ChangeType.MODIFY:
                resp = update_object(tm1_service=tm1_service, object_instance=obj, object_type=obj_type)
            elif action == ChangeType.REMOVE:
                resp = delete_object(tm1_service=tm1_service, object_instance=obj, object_type=obj_type)
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

            logger.info("%s %s%s -> %s %s",
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
            logger.exception("Exception during %s %s%s: %s",
                             action_name,
                             f"{obj_type}:" if obj_name else obj_type,
                             obj_name or "",
                             exc)
            if store is not None:
                store.end_operation_with_exception(exc)
                store.fail()
            return False, changes

    if store is not None:
        store.succeed() if ok_all else store.fail()

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
    for candidate in candidates:
        fn = getattr(module, candidate, None)
        if fn is not None:
            return fn
    raise AttributeError(
        f"No handler found for action='{action}', object_type='{object_type}' "
        f"in module '{module.__name__}'. Tried: {candidates}"
    )


def create_object(tm1_service: TM1Service, object_instance: T, object_type) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    create = _resolve_handler(module, "create", object_type)
    return create(tm1_service, object_instance)


def delete_object(tm1_service: TM1Service, object_instance: T, object_type) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    delete = _resolve_handler(module, "delete", object_type)
    return delete(tm1_service, object_instance)


def update_object(tm1_service: TM1Service, object_instance: T, object_type) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    update = _resolve_handler(module, "update", object_type)
    return update(tm1_service, object_instance)


def _cube_name_from_rule_source_path(source_path: str) -> str:
    normalized = (source_path or "").replace("\\", "/").lstrip("/")
    match = re.match(r"cubes/(.+)\.rules$", normalized)
    if not match:
        raise ValueError(f"Invalid rule source_path: '{source_path}'")
    return match.group(1)


def _prepare_execution_changes(changes: list[Change]) -> list[Change]:
    non_rule_changes: list[Change] = []
    rule_changes_by_cube: dict[str, Change] = {}

    for change in changes:
        if change.object_type == ObjectType.RULE:
            cube_name = _cube_name_from_rule_source_path(change.source_path)
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
                source_path=f"cubes/{cube_name}.json",
                body=Cube(
                    name=cube_name,
                    dimensions=[],
                    rules=cube_rules,
                    views=[],
                    source_path=f"cubes/{cube_name}.json",
                ),
            )
        )

    execution_changes = non_rule_changes + synthesized_cube_changes
    temp = Changeset()
    temp.changes = execution_changes
    temp.sort()
    return temp.changes


# --------------------------------------------------------------------------------
# Master TI for batch update and rollback functionality
# --------------------------------------------------------------------------------


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

        snippet = builder(obj)
        if snippet:
            ti_lines.append(snippet)
            ti_lines.append("")

    return "\r\n".join(ti_lines)


def apply_atomic(changeset: Changeset, tm1_service: TM1Service) -> bool:
    if not changeset.has_changes():
        return True

    # 0. Sort and validate the changeset
    changeset.sort()
    validate_changeset(tm1_service=tm1_service, changeset_object=changeset)

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
