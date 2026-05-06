from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tm1_git_py.services.changeset import Changeset


def load_changeset(changeset_id: str, *, base_dir: str | Path | None = None) -> Changeset:
    normalized_id = str(changeset_id or "").strip()
    if not normalized_id:
        raise ValueError("changeset_id is required")
    return Changeset.from_changeset_id(normalized_id, base_dir=str(base_dir) if base_dir else None)


def _normalize_change_apply(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "checked"}:
            return True
        if normalized in {"0", "false", "unchecked"}:
            return False
    return bool(value)


def _to_rule_segment(value: str) -> str:
    return (value or "").strip().replace("'", "''")


_FILTER_RULE_EXACT_URI_PATTERNS = [
    ("subsets", re.compile(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)/Subsets\('([^']+)'\)$", flags=re.IGNORECASE), lambda m: (m.group(1), m.group(2))),
    ("elements", re.compile(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)/Elements\('([^']+)'\)$", flags=re.IGNORECASE), lambda m: (m.group(1), m.group(2))),
    ("edges", re.compile(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)/Edges\((?:'([^']*)'/'([^']*)'|'([^'/]*)/([^']*)'|'([^']*)')\)$", flags=re.IGNORECASE), lambda m: (m.group(1), m.group(2))),
    ("hierarchies", re.compile(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)$", flags=re.IGNORECASE), lambda m: (m.group(1),)),
    ("dimensions", re.compile(r"^Dimensions\('([^']+)'\)$", flags=re.IGNORECASE), lambda _m: ()),
    ("views", re.compile(r"^Cubes\('([^']+)'\)/Views\('([^']+)'\)$", flags=re.IGNORECASE), lambda m: (m.group(1),)),
    ("rules", re.compile(r"^Cubes\('([^']*)'\)/Rules\('([^']*)'\)(?:\|.*)?$", flags=re.IGNORECASE), lambda m: (m.group(1),)),
    ("cubes", re.compile(r"^Cubes\('([^']+)'\)$", flags=re.IGNORECASE), lambda _m: ()),
    ("processes", re.compile(r"^Processes\('([^']+)'\)$", flags=re.IGNORECASE), lambda _m: ()),
    ("tasks", re.compile(r"^Chores\('([^']+)'\)/Tasks\('([^']+)'\)$", flags=re.IGNORECASE), lambda m: (m.group(1),)),
    ("chores", re.compile(r"^Chores\('([^']+)'\)$", flags=re.IGNORECASE), lambda _m: ()),
]

_FILTER_RULE_SCOPE_DEPTH = {
    "dimensions": 0,
    "hierarchies": 1,
    "subsets": 2,
    "elements": 2,
    "edges": 2,
    "cubes": 0,
    "views": 1,
    "rules": 1,
    "processes": 0,
    "chores": 0,
    "tasks": 1,
}


def _resolve_filter_rule_category_and_scope(uri: str):
    normalized_uri = str(uri or "").strip()
    if not normalized_uri:
        return None, ()
    for category, pattern, scope_builder in _FILTER_RULE_EXACT_URI_PATTERNS:
        match = pattern.fullmatch(normalized_uri)
        if match:
            return category, tuple(scope_builder(match))
    return None, ()


def _build_filter_wildcard_rule(category: str, scope_parts: tuple[str, ...]) -> str:
    quoted = lambda value: _to_rule_segment(value)
    if category == "dimensions":
        return "Dimensions('*')"
    if category == "hierarchies":
        return "Dimensions('*')/Hierarchies('*')" if len(scope_parts) == 0 else f"Dimensions('{quoted(scope_parts[0])}')/Hierarchies('*')"
    if category == "subsets":
        if len(scope_parts) == 0:
            return "Dimensions('*')/Hierarchies('*')/Subsets('*')"
        if len(scope_parts) == 1:
            return f"Dimensions('{quoted(scope_parts[0])}')/Hierarchies('*')/Subsets('*')"
        return f"Dimensions('{quoted(scope_parts[0])}')/Hierarchies('{quoted(scope_parts[1])}')/Subsets('*')"
    if category == "elements":
        if len(scope_parts) == 0:
            return "Dimensions('*')/Hierarchies('*')/Elements('*')"
        if len(scope_parts) == 1:
            return f"Dimensions('{quoted(scope_parts[0])}')/Hierarchies('*')/Elements('*')"
        return f"Dimensions('{quoted(scope_parts[0])}')/Hierarchies('{quoted(scope_parts[1])}')/Elements('*')"
    if category == "edges":
        if len(scope_parts) == 0:
            return "Dimensions('*')/Hierarchies('*')/Edges('*'/'*')"
        if len(scope_parts) == 1:
            return f"Dimensions('{quoted(scope_parts[0])}')/Hierarchies('*')/Edges('*'/'*')"
        return f"Dimensions('{quoted(scope_parts[0])}')/Hierarchies('{quoted(scope_parts[1])}')/Edges('*'/'*')"
    if category == "cubes":
        return "Cubes('*')"
    if category == "views":
        return "Cubes('*')/Views('*')" if len(scope_parts) == 0 else f"Cubes('{quoted(scope_parts[0])}')/Views('*')"
    if category == "rules":
        return "Cubes('*')/Rules('*')" if len(scope_parts) == 0 else f"Cubes('{quoted(scope_parts[0])}')/Rules('*')"
    if category == "processes":
        return "Processes('*')"
    if category == "chores":
        return "Chores('*')"
    if category == "tasks":
        return "Chores('*')/Tasks('*')" if len(scope_parts) == 0 else f"Chores('{quoted(scope_parts[0])}')/Tasks('*')"
    raise ValueError(f"Unsupported filter category: {category}")


def _compress_filter_records(category: str, records: list[dict], scope_parts: tuple[str, ...] = ()) -> list[str]:
    if not records:
        return []
    scope_depth = _FILTER_RULE_SCOPE_DEPTH[category]
    exact_rules = []
    if len(scope_parts) >= scope_depth:
        exact_rules = sorted(record["uri"] for record in records if not record["apply"])
    else:
        grouped = {}
        for record in records:
            group_key = record["scope_parts"][len(scope_parts)]
            grouped.setdefault(group_key, []).append(record)
        for group_key in sorted(grouped):
            exact_rules.extend(_compress_filter_records(category, grouped[group_key], scope_parts + (group_key,)))
    if not any(not record["apply"] for record in records):
        return exact_rules
    wildcard_rules = [_build_filter_wildcard_rule(category, scope_parts)] + [
        f"!{record['uri']}"
        for record in sorted(records, key=lambda item: item["uri"])
        if record["apply"]
    ]
    return wildcard_rules if len(wildcard_rules) < len(exact_rules) else exact_rules


def _split_filter_rule_prefix(rule: str) -> tuple[bool, str]:
    normalized_rule = str(rule or "").strip()
    if not normalized_rule:
        return False, ""
    if normalized_rule.startswith("!"):
        return True, normalized_rule[1:].lstrip("/")
    return False, normalized_rule


def _compose_filter_rule(pattern: str, is_include: bool) -> str:
    normalized_pattern = str(pattern or "").strip()
    if not normalized_pattern:
        return ""
    return f"!{normalized_pattern}" if is_include else normalized_pattern


def reduce_filter_rules(filter_rules):
    reduced_rules = []
    for raw_rule in filter_rules:
        normalized_rule = str(raw_rule or "").strip()
        if not normalized_rule:
            continue
        is_include, pattern = _split_filter_rule_prefix(normalized_rule)
        if not pattern:
            continue
        current_rule = _compose_filter_rule(pattern, is_include)
        opposite_rule = _compose_filter_rule(pattern, not is_include)
        reduced_rules = [rule for rule in reduced_rules if rule not in {current_rule, opposite_rule}]
        reduced_rules.append(current_rule)
    return reduced_rules


def derive_filter_rules_from_changeset(changeset_id_or_changeset: str | Changeset, *, base_dir: str | Path | None = None) -> list[str]:
    changeset_obj = (
        changeset_id_or_changeset
        if isinstance(changeset_id_or_changeset, Changeset)
        else load_changeset(str(changeset_id_or_changeset), base_dir=base_dir)
    )
    category_records = {}
    for change in list(getattr(changeset_obj, "changes", []) or []):
        uri = str(getattr(change, "uri", "") or "").strip()
        category, scope_parts = _resolve_filter_rule_category_and_scope(uri)
        if not uri or not category:
            continue
        category_records.setdefault(category, []).append({
            "uri": uri,
            "apply": _normalize_change_apply(getattr(change, "apply", True)),
            "scope_parts": scope_parts,
        })
    rules = []
    for category in sorted(category_records):
        rules.extend(_compress_filter_records(category, category_records[category]))
    return reduce_filter_rules(rules)
