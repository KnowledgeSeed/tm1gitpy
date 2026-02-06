import importlib
from typing import TypeVar, Dict

from TM1py import TM1Service

import tm1_git_py.changeset as ch
from tm1_git_py.model import Cube, Dimension, Process, Chore, Hierarchy, Subset, MDXView

T = TypeVar('T', Cube, Dimension, Process, Chore, Hierarchy, Subset, MDXView)

_PARENT_RELATIONS: Dict[str, str] = {
    "Hierarchy": "Dimension",
    "Subset": "Hierarchy",
    "MDXView": "Cube",
}

_CHILD_RELATIONS: Dict[str, str] = {
    "Dimension": "Hierarchy",
    "Hierarchy": "Subset",
    "Cube": "View",
}

_TYPE_MAP: Dict[str, str] = {
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
    get_parent_name = getattr(module, f"_{object_type}_context_from_path")
    parent_names = get_parent_name(object_instance.source_path)
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


def __build_args(object_instance: T, object_type: str, object_args: Dict[str, str] = None) -> Dict[str, str]:
    if object_type == "Process":
        object_args = {"name": object_instance.name}
    elif object_args is None:
        object_args = {f"{object_type.lower()}_name": object_instance.name}
    else:
        object_args[f"{object_type.lower()}_name"] = object_instance.name
    return object_args


def validate_existence_with_parent(tm1_service: TM1Service, object_instance: T, changeset_object: ch.Changeset):
    object_type = __normalize_for_view(object_instance)
    func_object_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[object_type]}"), "exists")
    object_args = __build_args(object_instance, object_type)

    parent_type = _PARENT_RELATIONS.get(object_instance.type)
    if parent_type:
        parent_args = __get_parent_name_args(object_instance=object_instance, parent_type=parent_type)
        object_args.update(parent_args)

        func_parent_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[parent_type]}"), "exists")
        parent_exists = func_parent_exists(**parent_args)
        parent_to_update = {
            f"{added.type}.{added.name}": added
            for added in changeset_object.added if added.type == parent_type
        }

        parent_name = parent_args.get(f"{parent_type.lower()}_name") or None
        parent_key = f"{parent_type}.{parent_name}"

        if not (parent_exists or parent_key in parent_to_update.keys()):
            raise ValueError(f"Cannot create {object_type}: '{object_instance.name}'. "
                             f"Parent {parent_type}: '{parent_name}' missing.")

    if func_object_exists(**object_args):
        raise ValueError(f"Cannot create {object_type}: '{object_instance.name}, {object_type} already exists.")



def validate_existence_with_children(tm1_service: TM1Service, object_instance: T, changeset_object: ch.Changeset):
    object_type = __normalize_for_view(object_instance)
    func_object_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[object_type]}"), "exists")
    object_args = __build_args(object_instance, object_type)

    parent_type = _PARENT_RELATIONS.get(object_instance.type)
    if parent_type:
        object_args = __get_parent_name_args(object_instance=object_instance, parent_type=parent_type)

    if not func_object_exists(**object_args):
        raise ValueError(f"Cannot update {object_type}: '{object_instance.name}, {object_type} does not exist.")

    child_type = _CHILD_RELATIONS.get(object_instance.type)
    if child_type:
        for child in getattr(object_instance, _TYPE_MAP[child_type]):
            func_child_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[child_type]}"), "exists")
            object_args = __build_args(child, child_type.lower(), object_args)
            child_exists = func_child_exists(**object_args)

            if not (child_exists or child in changeset_object.added):
                raise ValueError(f"Cannot update {object_type}: '{object_instance.name}' with {child_type}."
                                 f" {child_type}: '{child.name}' does not exist.")


def validate_existence(tm1_service: TM1Service, object_instance: T):
    object_type = __normalize_for_view(object_instance)
    func_object_exists = getattr(getattr(tm1_service, f"{_TYPE_MAP[object_type]}"), "exists")
    object_args = __build_args(object_instance, object_type)

    parent_type = _PARENT_RELATIONS.get(object_instance.type)
    if parent_type:
        object_args = __get_parent_name_args(object_instance=object_instance, parent_type=parent_type)

    if not func_object_exists(**object_args):
        raise ValueError(f"{object_type} to delete: '{object_instance.name} does not exist.")


def validate_changeset(tm1_service: TM1Service, changeset_object: ch.Changeset):
    for added in changeset_object.added:
        validate_existence_with_parent(tm1_service=tm1_service, object_instance=added, changeset_object=changeset_object)
    for modified in changeset_object.modified:
        validate_existence_with_children(tm1_service=tm1_service, object_instance=modified["new"], changeset_object=changeset_object)
    for removed in changeset_object.removed:
        validate_existence(tm1_service=tm1_service, object_instance=removed)
