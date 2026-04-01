from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, TypeVar, Optional

from TM1py import TM1Service

from tm1_git_py.changeset import ChangeType
from tm1_git_py.model import Cube, Dimension, Process, Chore, Hierarchy, Subset, MDXView

if TYPE_CHECKING:
    from tm1_git_py.changeset import Changeset

T = TypeVar('T', Cube, Dimension, Process, Chore, Hierarchy, Subset, MDXView)

_PARENT_RELATIONS: dict[str, str] = {
    "Hierarchy": "Dimension",
    "Subset": "Hierarchy",
    "MDXView": "Cube",
    #"Dimension": "Cube"
}

_CHILD_RELATIONS: dict[str, list[str]] = {
    "Dimension": ["Hierarchy"],
    "Hierarchy": ["Subset"],
    "Cube": ["View", "Dimension"],
}

_TYPE_MAP: dict[str, str] = {
    "Dimension": "dimensions",
    "Hierarchy": "hierarchies",
    "Subset": "subsets",
    "Cube": "cubes",
    "View": "views",
    "Process": "processes",
    "Chore": "chores"
}


def __get_parent_name_args(object_instance: T, parent_type: str) -> dict[str, str]:
    object_type = __normalize_for_view(object_instance).lower()
    parent_type = parent_type.lower()
    module = importlib.import_module(object_instance.__class__.__module__)
    get_parent_name = getattr(module, f"_{object_type}_context_from_uri")
    object_uri = getattr(object_instance, "uri", None)
    if callable(object_uri):
        return {}
    if not isinstance(object_uri, str) or not object_uri:
        return {}
    parent_names = get_parent_name(object_uri)
    if parent_type == "hierarchy":
        args = {
            f"dimension_name": parent_names[0],
            f"hierarchy_name": parent_names[1]
        }
    else:
        args = {
            f"{parent_type}_name": parent_names[0],
            f"{object_type}_name": parent_names[1]
        }
    return args


def __normalize_for_view(object_instance: T) -> str:
    object_type = object_instance.type
    if object_type == "MDXView":
        object_type = "View"
    return object_type


def __build_args(object_instance: T, object_type: str, object_args: dict[str, str] = None) -> dict[str, str]:
    if object_type == "Process":
        object_args = {"name": object_instance.name}
    elif object_args is None:
        object_args = {f"{object_type.lower()}_name": object_instance.name}
    else:
        object_args[f"{object_type.lower()}_name"] = object_instance.name
    return object_args


def validate_create(tm1_service: TM1Service, object_instance: T, changeset_object: Changeset):
    object_type = __normalize_for_view(object_instance)
    func_object_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[object_type]}"), "exists")
    object_args = __build_args(object_instance, object_type)
    to_be_added = [
        change.body
        for change in changeset_object.changes
        if ChangeType.from_raw(change.change_type) == ChangeType.ADD
    ]

    parent_type = _PARENT_RELATIONS.get(object_instance.type)
    if parent_type:
        parent_args = __get_parent_name_args(object_instance=object_instance, parent_type=parent_type)
        object_args.update(parent_args)

        func_parent_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[parent_type]}"), "exists")
        parent_exists = func_parent_exists(**parent_args)
        parent_to_update = {
            f"{added.type}.{added.name}": added
            for added in to_be_added if added.type == parent_type
        }

        parent_name = parent_args.get(f"{parent_type.lower()}_name") or None
        parent_key = f"{parent_type}.{parent_name}"

        if not (parent_exists or parent_key in parent_to_update.keys()):
            raise ValueError(f"Cannot create {object_type}: '{object_instance.name}'. "
                             f"Parent {parent_type}: '{parent_name}' missing.")

    child_types = _CHILD_RELATIONS.get(object_instance.type)
    if child_types:
        for child_type in child_types:
            for child in getattr(object_instance, _TYPE_MAP[child_type]):
                func_child_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[child_type]}"), "exists")
                object_args = __build_args(child, child_type.lower(), object_args)
                child_exists = func_child_exists(**object_args)

                if not (child_exists or child in to_be_added):
                    raise ValueError(f"Cannot update {object_type}: '{object_instance.name}' with {child_type}."
                                     f" {child_type}: '{child.name}' does not exist.")

    if func_object_exists(**object_args):
        raise ValueError(f"Cannot create {object_type}: '{object_instance.name}, {object_type} already exists.")



def validate_update(tm1_service: TM1Service, object_instance: T, changeset_object: Changeset):
    object_type = __normalize_for_view(object_instance)
    func_object_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[object_type]}"), "exists")
    object_args = __build_args(object_instance, object_type)

    parent_type = _PARENT_RELATIONS.get(object_instance.type)
    if parent_type:
        parent_args = __get_parent_name_args(object_instance=object_instance, parent_type=parent_type)
        object_args.update(parent_args)

    if not func_object_exists(**object_args):
        raise ValueError(f"Cannot update {object_type}: '{object_instance.name}, {object_type} does not exist.")

    to_be_added = [
        change.body
        for change in changeset_object.changes
        if ChangeType.from_raw(change.change_type) == ChangeType.ADD
    ]

    child_types = _CHILD_RELATIONS.get(object_instance.type)
    if child_types:
        for child_type in child_types:
            for child in getattr(object_instance, _TYPE_MAP[child_type]):
                func_child_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[child_type]}"), "exists")
                object_args = __build_args(child, child_type.lower(), object_args)
                child_exists = func_child_exists(**object_args)

                if not (child_exists or child in to_be_added):
                    raise ValueError(f"Cannot update {object_type}: '{object_instance.name}' with {child_type}."
                                     f" {child_type}: '{child.name}' does not exist.")


def validate_delete(tm1_service: TM1Service, object_instance: T):
    object_type = __normalize_for_view(object_instance)
    func_object_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[object_type]}"), "exists")
    object_args = __build_args(object_instance, object_type)

    parent_type = _PARENT_RELATIONS.get(object_instance.type)
    if parent_type:
        parent_args = __get_parent_name_args(object_instance=object_instance, parent_type=parent_type)
        object_args.update(parent_args)

    if not func_object_exists(**object_args):
        raise ValueError(f"{object_type} to delete: '{object_instance.name} does not exist.")


def validate_changeset(tm1_service: TM1Service, changeset_object: Changeset, fail_fast: Optional[bool] = True) -> list[str]:
    errors: list[str] = []

    changeset_object.errors.pop("validation", None)

    added_changes = [
        change for change in changeset_object.changes
        if ChangeType.from_raw(change.change_type) == ChangeType.ADD
    ]
    modified_changes = [
        change for change in changeset_object.changes
        if ChangeType.from_raw(change.change_type) == ChangeType.MODIFY
    ]
    removed_changes = [
        change for change in changeset_object.changes
        if ChangeType.from_raw(change.change_type) == ChangeType.REMOVE
    ]

    for added in added_changes:
        try:
            validate_create(
                tm1_service=tm1_service,
                object_instance=added.body,
                changeset_object=changeset_object
            )
        except Exception as exc:
            error_msg = str(exc)
            errors.append(error_msg)
            if fail_fast:
                changeset_object.errors["validation"] = errors
                raise ValueError(error_msg) from exc

    for modified in modified_changes:
        try:
            validate_update(
                tm1_service=tm1_service,
                object_instance=modified.body,
                changeset_object=changeset_object
            )
        except Exception as exc:
            error_msg = str(exc)
            errors.append(error_msg)
            if fail_fast:
                changeset_object.errors["validation"] = errors
                raise ValueError(error_msg) from exc

    for removed in removed_changes:
        try:
            validate_delete(tm1_service=tm1_service, object_instance=removed.body)
        except Exception as exc:
            error_msg = str(exc)
            errors.append(error_msg)
            if fail_fast:
                changeset_object.errors["validation"] = errors
                raise ValueError(error_msg) from exc

    if errors:
        changeset_object.errors["validation"] = errors
        if fail_fast:
            raise ValueError("; ".join(errors))

    return errors
