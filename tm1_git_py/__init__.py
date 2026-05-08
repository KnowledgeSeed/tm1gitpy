"""Utilities for serializing TM1 models to and from version control friendly formats."""
import os
import re
from importlib import import_module
from typing import Any, TYPE_CHECKING

__version__ = "1.2.0-rc1"

if TYPE_CHECKING:
    from tm1_git_py.seeder_hotpromote.hot_promote_filters import (
        derive_filter_rules_from_changeset,
        load_changeset,
    )
    from tm1_git_py.seeder_hotpromote.hot_promote_navigation import (
        get_navigation_items,
        get_navigation_overview,
        normalize_scope_type,
        parse_navigation_path,
    )
    from tm1_git_py.seeder_hotpromote.hot_promote_selection import (
        apply_selection_to_changes,
        extract_match_parts,
        matches_selection,
        resolve_selection_categories,
        update_changeset_apply,
        validate_selection_category,
    )
    from tm1_git_py.services import filter
    from tm1_git_py.services.changeset import Changeset
    from tm1_git_py.services.comparator import Comparator
    from tm1_git_py.services.deserializer import deserialize_model
    from tm1_git_py.services.exporter import export
    from tm1_git_py.services.filter import (
        FilterRules,
        filter as apply_filter,
        filter_changeset,
        should_exclude_path,
    )
    from tm1_git_py.services.serializer import serialize_model
    from tm1_git_py.tm1_api.element_service import (
        PaginatedElementsResult,
        get_elements,
    )
    from tm1_git_py.tm1_api.subset_service import (
        PaginatedSubsetsResult,
        get_subsets,
    )

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
    "update_changeset_apply",
    "apply_selection_to_changes",
    "extract_match_parts",
    "matches_selection",
    "resolve_selection_categories",
    "validate_selection_category",
    "get_navigation_items",
    "get_navigation_overview",
    "normalize_scope_type",
    "parse_navigation_path",
    "derive_filter_rules_from_changeset",
    "load_changeset",
    "serialize_model",
    "export",
]

_LAZY_IMPORTS = {
    "apply_filter": ("tm1_git_py.services.filter", "filter"),
    "apply_selection_to_changes": ("tm1_git_py.seeder_hotpromote.hot_promote_selection", "apply_selection_to_changes"),
    "Changeset": ("tm1_git_py.services.changeset", "Changeset"),
    "Comparator": ("tm1_git_py.services.comparator", "Comparator"),
    "derive_filter_rules_from_changeset": ("tm1_git_py.seeder_hotpromote.hot_promote_filters", "derive_filter_rules_from_changeset"),
    "deserialize_model": ("tm1_git_py.services.deserializer", "deserialize_model"),
    "export": ("tm1_git_py.services.exporter", "export"),
    "extract_match_parts": ("tm1_git_py.seeder_hotpromote.hot_promote_selection", "extract_match_parts"),
    "FilterRules": ("tm1_git_py.services.filter", "FilterRules"),
    "filter_changeset": ("tm1_git_py.services.filter", "filter_changeset"),
    "get_elements": ("tm1_git_py.tm1_api.element_service", "get_elements"),
    "get_navigation_items": ("tm1_git_py.seeder_hotpromote.hot_promote_navigation", "get_navigation_items"),
    "get_navigation_overview": ("tm1_git_py.seeder_hotpromote.hot_promote_navigation", "get_navigation_overview"),
    "get_subsets": ("tm1_git_py.tm1_api.subset_service", "get_subsets"),
    "load_changeset": ("tm1_git_py.seeder_hotpromote.hot_promote_filters", "load_changeset"),
    "matches_selection": ("tm1_git_py.seeder_hotpromote.hot_promote_selection", "matches_selection"),
    "normalize_scope_type": ("tm1_git_py.seeder_hotpromote.hot_promote_navigation", "normalize_scope_type"),
    "PaginatedElementsResult": ("tm1_git_py.tm1_api.element_service", "PaginatedElementsResult"),
    "PaginatedSubsetsResult": ("tm1_git_py.tm1_api.subset_service", "PaginatedSubsetsResult"),
    "parse_navigation_path": ("tm1_git_py.seeder_hotpromote.hot_promote_navigation", "parse_navigation_path"),
    "resolve_selection_categories": ("tm1_git_py.seeder_hotpromote.hot_promote_selection", "resolve_selection_categories"),
    "serialize_model": ("tm1_git_py.services.serializer", "serialize_model"),
    "should_exclude_path": ("tm1_git_py.services.filter", "should_exclude_path"),
    "update_changeset_apply": ("tm1_git_py.seeder_hotpromote.hot_promote_selection", "update_changeset_apply"),
    "validate_selection_category": ("tm1_git_py.seeder_hotpromote.hot_promote_selection", "validate_selection_category"),
}


def __getattr__(name: str) -> Any:
    if name == "filter":
        module = import_module("tm1_git_py.services.filter")
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
