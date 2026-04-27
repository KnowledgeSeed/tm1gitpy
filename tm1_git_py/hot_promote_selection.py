from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from tm1_git_py.changeset import Changeset, ObjectType
from tm1_git_py.changeset_store import ChangesetStore

logger = logging.getLogger(__name__)

SUPPORTED_SELECTION_CATEGORIES = {
    "dimensions": ObjectType.DIMENSION,
    "hierarchies": ObjectType.HIERARCHY,
    "subsets": ObjectType.SUBSET,
    "elements": ObjectType.ELEMENT,
    "edges": ObjectType.EDGE,
    "cubes": ObjectType.CUBE,
    "mdxviews": ObjectType.MDX_VIEW,
    "nativeviews": ObjectType.NATIVE_VIEW,
    "processes": ObjectType.PROCESS,
    "chores": ObjectType.CHORE,
    "rules": ObjectType.RULE,
    "tasks": ObjectType.CHORE,
}

_SELECTION_PATTERNS = [
    (re.compile(r"^Dimensions\('([^']+|\*)'\)/Hierarchies\('([^']+|\*)'\)/Subsets\('([^']+|\*)'\)$", flags=re.IGNORECASE), ["subsets"]),
    (re.compile(r"^Dimensions\('([^']+|\*)'\)/Hierarchies\('([^']+|\*)'\)/Elements\('([^']+|\*)'\)$", flags=re.IGNORECASE), ["elements"]),
    (re.compile(r"^Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)/Edges\((?:'([^']*)'/'([^']*)'|'([^'/]*)/([^']*)'|'([^']*)')\)$", flags=re.IGNORECASE), ["edges"]),
    (re.compile(r"^Dimensions\('([^']+|\*)'\)/Hierarchies\('([^']+|\*)'\)$", flags=re.IGNORECASE), ["hierarchies"]),
    (re.compile(r"^Dimensions\('([^']+|\*)'\)$", flags=re.IGNORECASE), ["dimensions"]),
    (re.compile(r"^Cubes\('([^']+|\*)'\)/Views\('([^']+|\*)'\)$", flags=re.IGNORECASE), ["mdxviews", "nativeviews"]),
    (re.compile(r"^Cubes\('([^']*)'\)/Rules\('([^']*)'\)(?:\|.*)?$", flags=re.IGNORECASE), ["rules"]),
    (re.compile(r"^Cubes\('([^']+|\*)'\)$", flags=re.IGNORECASE), ["cubes"]),
    (re.compile(r"^Processes\('([^']+|\*)'\)$", flags=re.IGNORECASE), ["processes"]),
    (re.compile(r"^Chores\('([^']+|\*)'\)/Tasks\('([^']+|\*)'\)$", flags=re.IGNORECASE), ["tasks"]),
    (re.compile(r"^Chores\('([^']+|\*)'\)$", flags=re.IGNORECASE), ["chores"]),
]

_MATCHERS = {
    "dimensions": re.compile(r"^Dimensions\('([^']+|\*)'\)$", flags=re.IGNORECASE),
    "hierarchies": re.compile(r"^Dimensions\('([^']+|\*)'\)/Hierarchies\('([^']+|\*)'\)$", flags=re.IGNORECASE),
    "subsets": re.compile(r"^Dimensions\('([^']+|\*)'\)/Hierarchies\('([^']+|\*)'\)/Subsets\('([^']+|\*)'\)$", flags=re.IGNORECASE),
    "elements": re.compile(r"^Dimensions\('([^']+|\*)'\)/Hierarchies\('([^']+|\*)'\)/Elements\('([^']+|\*)'\)$", flags=re.IGNORECASE),
    "edges": re.compile(r"^Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)/Edges\((?:'([^']*)'/'([^']*)'|'([^'/]*)/([^']*)'|'([^']*)')\)$", flags=re.IGNORECASE),
    "cubes": re.compile(r"^Cubes\('([^']+|\*)'\)$", flags=re.IGNORECASE),
    "mdxviews": re.compile(r"^Cubes\('([^']+|\*)'\)/Views\('([^']+|\*)'\)$", flags=re.IGNORECASE),
    "nativeviews": re.compile(r"^Cubes\('([^']+|\*)'\)/Views\('([^']+|\*)'\)$", flags=re.IGNORECASE),
    "rules": re.compile(r"^Cubes\('([^']*)'\)/Rules\('([^']*)'\)(?:\|.*)?$", flags=re.IGNORECASE),
    "processes": re.compile(r"^Processes\('([^']+|\*)'\)$", flags=re.IGNORECASE),
    "chores": re.compile(r"^Chores\('([^']+|\*)'\)$", flags=re.IGNORECASE),
    "tasks": re.compile(r"^Chores\('([^']+|\*)'\)/Tasks\('([^']+|\*)'\)$", flags=re.IGNORECASE),
}

_SQL_SELECTION_COLUMNS = {
    "dimensions": ("dim_name",),
    "hierarchies": ("dim_name", "hier_name"),
    "subsets": ("dim_name", "hier_name", "object_name"),
    "elements": ("dim_name", "hier_name", "object_name"),
    "cubes": ("cube_name",),
    "mdxviews": ("cube_name", "object_name"),
    "nativeviews": ("cube_name", "object_name"),
    "rules": ("cube_name", "object_name"),
    "processes": ("process_name",),
    "chores": ("chore_name",),
    "tasks": ("chore_name", "object_name"),
}


def validate_selection_category(category: str) -> str:
    normalized = (category or "").strip().lower()
    if normalized not in SUPPORTED_SELECTION_CATEGORIES:
        raise ValueError(f"Unsupported selection category '{category}'.")
    return normalized


def resolve_selection_categories(uri: str) -> list[str]:
    normalized_uri = str(uri or "").strip()
    for pattern, categories in _SELECTION_PATTERNS:
        if pattern.fullmatch(normalized_uri):
            return list(categories)
    raise ValueError("Invalid TM1 object uri")


def _normalize_match_groups(category: str, groups: tuple[str, ...]) -> tuple[str, ...]:
    if category != "edges":
        return tuple(str(group) for group in groups)
    dimension, hierarchy, source_a, target_a, source_b, target_b, single = groups
    if source_a is not None and target_a is not None:
        return str(dimension), str(hierarchy), str(source_a), str(target_a)
    if source_b is not None and target_b is not None:
        return str(dimension), str(hierarchy), str(source_b), str(target_b)
    single_value = str(single or "")
    if single_value == "*":
        return str(dimension), str(hierarchy), "*", "*"
    if "/" in single_value:
        source_name, target_name = single_value.split("/", 1)
        return str(dimension), str(hierarchy), str(source_name), str(target_name)
    return str(dimension), str(hierarchy), single_value, ""


def extract_match_parts(category: str, uri: str) -> tuple[str, ...]:
    normalized_category = validate_selection_category(category)
    pattern = _MATCHERS.get(normalized_category)
    if pattern is None:
        raise ValueError(f"Unsupported Hot Promote selection category: {category}")
    match = pattern.fullmatch(str(uri or "").strip())
    if not match:
        raise ValueError("Invalid TM1 object uri")
    return _normalize_match_groups(normalized_category, match.groups())


def matches_selection(category: str, selection_uri: str, change_uri: str) -> bool:
    try:
        selection_parts = extract_match_parts(category, selection_uri)
        change_parts = extract_match_parts(category, change_uri)
    except ValueError:
        return False
    return all(expected == "*" or expected == actual for expected, actual in zip(selection_parts, change_parts))


def apply_scoped_selection(changes: list, category: str, uri: str, apply: bool) -> int:
    normalized_category = validate_selection_category(category)
    target_object_type = SUPPORTED_SELECTION_CATEGORIES[normalized_category]
    updated = 0
    for change in changes:
        if getattr(change, "object_type", None) != target_object_type:
            continue
        if matches_selection(normalized_category, uri, getattr(change, "uri", "")):
            change.apply = apply
            updated += 1
    return updated


def apply_selection_to_changes(changes: list, uri: str, apply: bool) -> int:
    updated = 0
    for category in resolve_selection_categories(uri):
        updated += int(apply_scoped_selection(changes, category, uri, apply) or 0)
    return updated


def _escape_like_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_sqlite_apply_selection_predicate(category: str, uri: str):
    object_type = SUPPORTED_SELECTION_CATEGORIES.get(category)
    if object_type is None:
        return None
    parts = extract_match_parts(category, uri)
    clauses = ["object_type = ?"]
    params: list[Any] = [object_type.value]
    if category == "edges":
        dimension, hierarchy, source, target = parts
        for column, value in (("dim_name", dimension), ("hier_name", hierarchy)):
            if value != "*":
                clauses.append(f"{column} = ?")
                params.append(value)
        if source != "*" and target != "*":
            clauses.append("object_name = ?")
            params.append(f"{source}:{target}")
        elif source != "*":
            clauses.append("object_name LIKE ? ESCAPE '\\'")
            params.append(f"{_escape_like_pattern(source)}:%")
        elif target != "*":
            clauses.append("object_name LIKE ? ESCAPE '\\'")
            params.append(f"%:{_escape_like_pattern(target)}")
        return clauses, params
    columns = _SQL_SELECTION_COLUMNS.get(category)
    if not columns or len(columns) != len(parts):
        return None
    for column, value in zip(columns, parts):
        if value == "*":
            continue
        clauses.append(f"{column} = ?")
        params.append(value)
    return clauses, params


def _update_changeset_apply_direct(changeset_id: str, uri: str, apply: bool, *, base_dir: str | Path | None = None) -> int:
    sqlite_path = ChangesetStore.path_for(changeset_id=changeset_id, base_dir=str(base_dir) if base_dir else None)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"Changeset sqlite not found for id '{changeset_id}': {sqlite_path}")
    desired_apply = 1 if apply else 0
    updated_count = 0
    conn = sqlite3.connect(str(sqlite_path), timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        for category in resolve_selection_categories(uri):
            predicate = _build_sqlite_apply_selection_predicate(category, uri)
            if predicate is None:
                raise ValueError(f"Unsupported Hot Promote selection category: {category}")
            clauses, params = predicate
            cursor = conn.execute(
                "UPDATE changes SET apply = ? WHERE apply != ? AND " + " AND ".join(clauses),
                [desired_apply, desired_apply, *params],
            )
            updated_count += int(cursor.rowcount or 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return updated_count


def _update_changeset_apply_materialized(changeset_id: str, uri: str, apply: bool, *, base_dir: str | Path | None = None) -> int:
    changeset_obj = Changeset.from_changeset_id(changeset_id, base_dir=str(base_dir) if base_dir else None)
    changes = list(changeset_obj.changes)
    updated_count = int(apply_selection_to_changes(changes, uri, apply) or 0)
    changeset_obj.changes = changes
    return updated_count


def update_changeset_apply(
    changeset_id: str,
    uri: str,
    apply: bool,
    *,
    base_dir: str | Path | None = None,
) -> int:
    if not isinstance(apply, bool):
        raise ValueError("apply must be a boolean")
    normalized_id = str(changeset_id or "").strip()
    normalized_uri = str(uri or "").strip()
    if not normalized_id:
        raise ValueError("Invalid changeset_id")
    if not normalized_uri:
        raise ValueError("Invalid uri")
    try:
        return _update_changeset_apply_direct(normalized_id, normalized_uri, apply, base_dir=base_dir)
    except ValueError:
        raise
    except (sqlite3.Error, OSError) as exc:
        logger.info(
            "Falling back to materialized apply-selection for %s after direct update failed: %s",
            normalized_id,
            exc,
            exc_info=True,
        )
        return _update_changeset_apply_materialized(normalized_id, normalized_uri, apply, base_dir=base_dir)
