import importlib
import logging
from pathlib import Path
from typing import Optional, Union, Any, TypeVar

import TM1py
from TM1py import TM1Service
from requests import Response

from tm1_git_py import Changeset
from tm1_git_py.changeset_status import ChangeSetStatusStore
from tm1_git_py.model import Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore
from tm1_git_py.model.hierarchy import build_hierarchy_create_ti
from tm1_git_py.validation import validate_changeset

logger = logging.getLogger(__name__)

T = TypeVar('T', Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore)


def apply(
        changeset: Changeset,
        tm1_service: TM1Service,
        *,
        status_dir: Optional[Union[str, Path]] = None,
        execution_id: Optional[str] = None,
        changeset_name: Optional[str] = None,
        fail_fast: bool = True,
        **kwargs
) -> tuple[bool, Union[list, None]]:

    changes = []
    if not changeset.has_changes():
        logger.info("No changes to apply.")
        return True, None

    changeset.sort()
    validate_errors = validate_changeset(
        tm1_service=tm1_service,
        changeset_object=changeset,
        fail_fast=fail_fast
    )
    if validate_errors:
        logger.warning("Changeset validation reported %d error(s).", len(validate_errors))

    operations: list[tuple[str, Any]] = []
    operations += [("CREATE", obj) for obj in changeset.added]
    operations += [("UPDATE", obj) for obj in changeset.modified]
    operations += [("DELETE", obj) for obj in changeset.removed]

    store: Optional[ChangeSetStatusStore] = None
    if status_dir is not None:
        store = ChangeSetStatusStore(status_dir=status_dir, execution_id=execution_id,
                                     changeset_name=changeset_name)
        store.start(total_operations=len(operations))
        changeset.last_execution_id = store.execution_id
        logger.info("changeset execution_id=%s status_file=%s", store.execution_id, store.path)

    def _obj_meta(o: Any) -> tuple[str, Optional[str], Optional[str]]:
        if isinstance(o, dict) and "new" in o:
            o = o["new"]
        return o.__class__.__name__, getattr(o, "name", None), getattr(o, "source_path", None)

    ok_all = True

    for i, (action, obj) in enumerate(operations, start=1):
        obj_type, obj_name, obj_path = _obj_meta(obj)

        if store is not None:
            store.begin_operation(i, action, obj_type, obj_name, obj_path)

        try:
            if action == "CREATE":
                resp = create_object(tm1_service=tm1_service, object_instance=obj)
            elif action == "UPDATE":
                resp = update_object(tm1_service=tm1_service, object_instance=obj, **kwargs)
            elif action == "DELETE":
                resp = delete_object(tm1_service=tm1_service, object_instance=obj)
            else:
                raise ValueError(f"Unknown action: {action}")

            changes.append(resp.url)

            logger.info("%s %s%s -> %s %s",
                        action,
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
                             action,
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

def create_object(tm1_service: TM1Service, object_instance: T) -> Response:
    object_type = object_instance.type.lower()

    if object_type == "mdxview":
        object_type = "mdx_view"

    create = getattr(object_instance, f"create_{object_type}")
    return create(tm1_service, object_instance)


def delete_object(tm1_service: TM1Service, object_instance: T) -> Response:
    object_type = object_instance.type.lower()

    if object_type == "mdxview":
        object_type = "mdx_view"

    delete = getattr(object_instance, f"delete_{object_type}")
    return delete(tm1_service, object_instance)


def update_object(tm1_service: TM1Service, object_instance: dict[T, Any], **kwargs) -> Response:
    object_type = object_instance['new'].type.lower()

    if object_type == "mdxview":
        object_type = "mdx_view"

    update = getattr(object_instance, f"update_{object_type}")

    if object_type == "cube":
        return update(tm1_service, object_instance, **kwargs)

    return update(tm1_service, object_instance)


# --------------------------------------------------------------------------------
# Master TI for batch update and rollback functionality
# --------------------------------------------------------------------------------

def build_master_changeset_ti(changeset: Changeset) -> str:
    """
    Compiles a Changeset object into a single Atomic TurboIntegrator script.
    """
    ti_lines = []

    ti_lines.append("# **** Atomic Changeset Execution ****")
    ti_lines.append("")

    operations: list[tuple[str, Any]] = []
    operations += [("CREATE", obj) for obj in changeset.added]
    operations += [("UPDATE", obj) for obj in changeset.modified]
    operations += [("DELETE", obj) for obj in changeset.removed]

    for action, obj in operations:
        snippet = ""

        if isinstance(obj, dict):
            object_type = obj["new"].type.lower()
            module = importlib.import_module(obj["new"].__class__.__module__)
        else:
            object_type = obj.type.lower()
            module = importlib.import_module(obj.__class__.__module__)


        if action == "CREATE":
            create = getattr(module, f"build_{object_type}_create_ti")
            snippet = create(obj)
        elif action == "DELETE":
            update = getattr(module, f"build_{object_type}_delete_ti")
            snippet = update(obj)
        elif action == "UPDATE":
            delete = getattr(module, f"build_{object_type}_update_ti")
            snippet = delete(obj)

        if snippet:
            ti_lines.append(snippet)
            ti_lines.append("")

    return "\r\n".join(ti_lines)


import uuid


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
