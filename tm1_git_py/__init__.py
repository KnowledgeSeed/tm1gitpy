"""Utilities for serializing TM1 models to and from version control friendly formats."""
import os
import re
from importlib import import_module
from typing import Any, TYPE_CHECKING

__version__ = "0.1.0"

if TYPE_CHECKING:
    from tm1_git_py.changeset import Changeset
    from tm1_git_py.comparator import Comparator
    from tm1_git_py.deserializer import deserialize_model
    from tm1_git_py.tm1py_ext.element_service_ext import (
        PaginatedElementsResult,
        get_elements,
    )
    from tm1_git_py.tm1py_ext.subset_service_ext import (
        PaginatedSubsetsResult,
        get_subsets,
    )
    from tm1_git_py.exporter import export
    from tm1_git_py.filter import filter as apply_filter
    from tm1_git_py.filter import FilterRules, filter_changeset, should_exclude_path
    from tm1_git_py.serializer import serialize_model

__all__ = [
    "Changeset",
    "Comparator",
    "deserialize_model",
    "filter",
    "get_elements",
    "PaginatedElementsResult",
    "get_subsets",
    "PaginatedSubsetsResult",
    "apply_filter",
    "FilterRules",
    "filter_changeset",
    "should_exclude_path",
    "serialize_model",
    "export",
]

_LAZY_IMPORTS = {
    "Changeset": ("tm1_git_py.changeset", "Changeset"),
    "get_elements": ("tm1_git_py.tm1py_ext.element_service_ext", "get_elements"),
    "PaginatedElementsResult": ("tm1_git_py.tm1py_ext.element_service_ext", "PaginatedElementsResult"),
    "get_subsets": ("tm1_git_py.tm1py_ext.subset_service_ext", "get_subsets"),
    "PaginatedSubsetsResult": ("tm1_git_py.tm1py_ext.subset_service_ext", "PaginatedSubsetsResult"),
    "Comparator": ("tm1_git_py.comparator", "Comparator"),
    "deserialize_model": ("tm1_git_py.deserializer", "deserialize_model"),
    "apply_filter": ("tm1_git_py.filter", "filter"),
    "FilterRules": ("tm1_git_py.filter", "FilterRules"),
    "filter_changeset": ("tm1_git_py.filter", "filter_changeset"),
    "should_exclude_path": ("tm1_git_py.filter", "should_exclude_path"),
    "serialize_model": ("tm1_git_py.serializer", "serialize_model"),
    "export": ("tm1_git_py.exporter", "export"),
}


def __getattr__(name: str) -> Any:
    if name == "filter":
        module = import_module("tm1_git_py.filter")
        globals()[name] = module
        return module

    if name in _LAZY_IMPORTS:
        module_name, attr_name = _LAZY_IMPORTS[name]
        module = import_module(module_name)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))


def update_version(new_version):
    version_file = os.path.join(os.path.dirname(__file__), '__init__.py')
    with open(version_file, 'r') as f:
        content = f.read()
    content_new = re.sub(r'__version__ = ["\'].*["\']', f'__version__ = "{new_version}"', content, 1)
    with open(version_file, 'w') as f:
        f.write(content_new)


def get_version():
    return __version__
