import logging
from pathlib import Path
from typing import Optional, Union, Any, TypeVar

from TM1py import TM1Service
from requests import Response

from tm1_git_py import Changeset
from tm1_git_py.changeset_status import ChangeSetStatusStore
from tm1_git_py.model import Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore
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
    """    
    validate_errors = validate_changeset(
        tm1_service=tm1_service,
        changeset_object=changeset,
        fail_fast=fail_fast
    )
    if validate_errors:
        logger.warning("Changeset validation reported %d error(s).", len(validate_errors))
    """
    operations: list[tuple[str, Any]] = []
    #operations += [("CREATE", obj) for obj in changeset.added]
    #operations += [("UPDATE", obj) for obj in changeset.modified]
    #operations += [("DELETE", obj) for obj in changeset.removed]

    store: Optional[ChangeSetStatusStore] = None
    if status_dir is not None:
        store = ChangeSetStatusStore(status_dir=status_dir, execution_id=execution_id,
                                     changeset_name=changeset_name)
        store.start(total_operations=len(changeset.changes))
        changeset.last_execution_id = store.execution_id
        logger.info("changeset execution_id=%s status_file=%s", store.execution_id, store.path)

    def _obj_meta(o: Any) -> tuple[str, Optional[str], Optional[str]]:
        if isinstance(o, dict) and "new" in o:
            o = o["new"]
        return o.__class__.__name__, getattr(o, "name", None), getattr(o, "source_path", None)

    ok_all = True

    for i, change in enumerate(changeset.changes, start=1):
        obj = change.body
        action = change.change_type.value
        obj_type = change.object_type.value
        obj_path = change.source_path
        obj_name = obj.name

        if store is not None:
            store.begin_operation(i, action, obj_type, obj_name, obj_path)

        try:
            if action == "add":
                resp = create_object(tm1_service=tm1_service, object_instance=obj, object_type=obj_type)
            elif action == "modify":
                resp = update_object(tm1_service=tm1_service, object_instance={"old": obj, "new": obj}, object_type=obj_type)
            elif action == "remove":
                resp = delete_object(tm1_service=tm1_service, object_instance=obj, object_type=obj_type)
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

def create_object(tm1_service: TM1Service, object_instance: T, object_type) -> Response:
    create = getattr(object_instance, f"create_{object_type.lower()}")
    return create(tm1_service, object_instance)


def delete_object(tm1_service: TM1Service, object_instance: T, object_type) -> Response:
    delete = getattr(object_instance, f"delete_{object_type.lower()}")
    return delete(tm1_service, object_instance)


def update_object(tm1_service: TM1Service, object_instance: dict[T, Any], object_type) -> Response:
    update = getattr(object_instance, f"update_{object_type.lower()}")
    return update(tm1_service, object_instance)
