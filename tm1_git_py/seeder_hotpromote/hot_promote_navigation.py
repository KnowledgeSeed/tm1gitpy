from __future__ import annotations

import orjson
import re
import sqlite3
from pathlib import Path
from typing import Any

from tm1_git_py.db.changeset_store import ChangesetStore


STATUS_PRIORITY = ("remove", "modify", "add")
NAVIGATION_PATH_KEYS = (
    "dimension",
    "hierarchy",
    "subset",
    "element",
    "edge",
    "cube",
    "view",
    "rule",
    "process",
    "chore",
    "task",
)
OBJECT_TYPES = {
    "cube": "Cube",
    "chore": "Chore",
    "mdx_view": "MDXView",
    "native_view": "NativeView",
    "dimension": "Dimension",
    "hierarchy": "Hierarchy",
    "subset": "Subset",
    "element": "Element",
    "edge": "Edge",
    "rule": "Rule",
    "process": "Process",
}
VIEW_OBJECT_TYPES = (OBJECT_TYPES["mdx_view"], OBJECT_TYPES["native_view"])


def normalize_scope_type(scope_type: str | None) -> str:
    normalized = str(scope_type or "overview").strip().lower()
    aliases = {
        "dimension": "dimensions",
        "dimensions": "dimensions",
        "hierarchy": "hierarchies",
        "hierarchies": "hierarchies",
        "subset": "subsets",
        "subsets": "subsets",
        "element": "elements",
        "elements": "elements",
        "edge": "edges",
        "edges": "edges",
        "cube": "cubes",
        "cubes": "cubes",
        "rule": "rules",
        "rules": "rules",
        "view": "views",
        "views": "views",
        "mdxview": "views",
        "mdxviews": "views",
        "nativeview": "views",
        "nativeviews": "views",
        "process": "processes",
        "processes": "processes",
        "chore": "chores",
        "chores": "chores",
        "task": "tasks",
        "tasks": "tasks",
        "overview": "overview",
    }
    return aliases.get(normalized, normalized)


def parse_navigation_path(source: Any) -> dict[str, str]:
    path: dict[str, str] = {}
    getter = getattr(source, "get", None)
    if getter is None and isinstance(source, dict):
        getter = source.get
    if getter is None:
        return path
    for key in NAVIGATION_PATH_KEYS:
        value = getter(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            path[key] = text
    return path


def _resolve_changeset_db_path(changeset_id_or_db_path: str | Path, *, base_dir: str | Path | None = None) -> Path:
    if isinstance(changeset_id_or_db_path, Path):
        return changeset_id_or_db_path

    value = str(changeset_id_or_db_path or "").strip()
    if not value:
        raise ValueError("changeset_id or db_path is required")

    candidate = Path(value)
    if candidate.suffix.lower() in {".sqlite", ".db"} or candidate.exists():
        return candidate

    return ChangesetStore.path_for(changeset_id=value, base_dir=str(base_dir) if base_dir else None)


def _sql_string_list(values: tuple[str, ...] | list[str]) -> str:
    quoted: list[str] = []
    for value in values:
        quoted.append("'" + str(value).replace("'", "''") + "'")
    return ", ".join(quoted)


def _tm1_quote(value: str) -> str:
    return str(value or "").replace("'", "''")


def _status_from_counts(row: sqlite3.Row | dict[str, Any]) -> str:
    for status in STATUS_PRIORITY:
        if int(row[f"{status}_count"] or 0) > 0:
            return status
    return "modify"


def _apply_state_from_bounds(row: sqlite3.Row | dict[str, Any]) -> str:
    min_apply = int(row["min_apply"] or 0)
    max_apply = int(row["max_apply"] or 0)
    if min_apply == 1 and max_apply == 1:
        return "checked"
    if min_apply == 0 and max_apply == 0:
        return "unchecked"
    return "mixed"


def _readonly_connection(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _execute_grouped_query(
    conn: sqlite3.Connection,
    *,
    group_expr: str,
    where_clauses: list[str],
    params: list[Any],
    limit: int,
    offset: int,
) -> list[sqlite3.Row]:
    where_sql = ""
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)
    sql = f"""
        SELECT
            {group_expr} AS group_name,
            COUNT(*) AS matched_count,
            MIN(apply) AS min_apply,
            MAX(apply) AS max_apply,
            SUM(CASE WHEN lower(change_type) = 'remove' THEN 1 ELSE 0 END) AS remove_count,
            SUM(CASE WHEN lower(change_type) = 'modify' THEN 1 ELSE 0 END) AS modify_count,
            SUM(CASE WHEN lower(change_type) = 'add' THEN 1 ELSE 0 END) AS add_count,
            MIN(uri) AS sample_uri,
            MIN(body_json) AS sample_body_json,
            MIN(COALESCE(json_extract(body_json, '$.source_name'), '')) AS sample_source_name,
            MIN(COALESCE(json_extract(body_json, '$.target_name'), '')) AS sample_target_name
        FROM changes
        {where_sql}
        GROUP BY {group_expr}
        ORDER BY lower({group_expr}) ASC, {group_expr} ASC
        LIMIT ? OFFSET ?
    """
    query_params = list(params) + [int(limit), max(0, int(offset))]
    return conn.execute(sql, tuple(query_params)).fetchall()


def _execute_grouped_count_query(
    conn: sqlite3.Connection,
    *,
    group_expr: str,
    where_clauses: list[str],
    params: list[Any],
) -> int:
    where_sql = ""
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)
    sql = f"""
        SELECT COUNT(*) AS total_count
        FROM (
            SELECT {group_expr} AS group_name
            FROM changes
            {where_sql}
            GROUP BY {group_expr}
        ) grouped_changes
    """
    row = conn.execute(sql, tuple(params)).fetchone()
    return int((row["total_count"] if row else 0) or 0)


def _normalize_search_term(search: str | None) -> str:
    return str(search or "").strip()


def _escape_like_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _append_search_clause(stage: dict[str, Any], search: str) -> dict[str, Any]:
    normalized_search = _normalize_search_term(search)
    if not normalized_search:
        return {
            **stage,
            "search": "",
        }
    where = list(stage["where"])
    params = list(stage["params"])
    like_pattern = "%" + _escape_like_pattern(normalized_search.lower()) + "%"
    where.append(f"LOWER(COALESCE({stage['group_expr']}, '')) LIKE ? ESCAPE '\\'")
    params.append(like_pattern)
    return {
        **stage,
        "where": where,
        "params": params,
        "search": normalized_search,
    }


def _format_edge_label(source_name: str, target_name: str, fallback: str = "") -> str:
    source = str(source_name or "").strip()
    target = str(target_name or "").strip()
    if source and target:
        return f"{source} -> {target}"
    if source:
        return source
    if target:
        return target
    return str(fallback or "").strip()


_EDGE_URI_RE = re.compile(
    r"^Dimensions\('((?:[^']|'')*)'\)/Hierarchies\('((?:[^']|'')*)'\)/Edges\('((?:[^']|'')*)'/'((?:[^']|'')*)'\)$",
    flags=re.IGNORECASE,
)


def _tm1_unquote(value: str) -> str:
    return str(value or "").replace("''", "'")


def _parse_edge_uri(uri: str) -> tuple[str, str] | None:
    match = _EDGE_URI_RE.match(str(uri or "").strip())
    if not match:
        return None
    return _tm1_unquote(match.group(3)), _tm1_unquote(match.group(4))


def _resolve_edge_names(row: sqlite3.Row | dict[str, Any], body: dict[str, Any] | None = None) -> tuple[str, str]:
    payload = body or {}
    source_name = str(row.get("sample_source_name") if isinstance(row, dict) else row["sample_source_name"] or "").strip()
    target_name = str(row.get("sample_target_name") if isinstance(row, dict) else row["sample_target_name"] or "").strip()
    if not source_name:
        source_name = str(payload.get("source_name") or "").strip()
    if not target_name:
        target_name = str(payload.get("target_name") or "").strip()
    if not source_name or not target_name:
        sample_uri = str(row.get("sample_uri") if isinstance(row, dict) else row["sample_uri"] or "").strip()
        parsed_edge = _parse_edge_uri(sample_uri)
        if parsed_edge:
            parsed_source, parsed_target = parsed_edge
            if not source_name:
                source_name = parsed_source
            if not target_name:
                target_name = parsed_target
    return source_name, target_name


def _build_overview_item(category: str, row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    wildcard_uri = {
        "dimensions": "Dimensions('*')",
        "hierarchies": "Dimensions('*')/Hierarchies('*')",
        "subsets": "Dimensions('*')/Hierarchies('*')/Subsets('*')",
        "elements": "Dimensions('*')/Hierarchies('*')/Elements('*')",
        "edges": "Dimensions('*')/Hierarchies('*')/Edges('*'/'*')",
        "cubes": "Cubes('*')",
        "rules": "Cubes('*')/Rules('*')",
        "views": "Cubes('*')/Views('*')",
        "processes": "Processes('*')",
        "chores": "Chores('*')",
    }[category]
    item = {
        "scope_type": category,
        "uri": wildcard_uri,
        "matched_count": 0,
        "status": None,
        "apply_state": "unchecked",
    }
    if row is None:
        return item
    item["matched_count"] = int(row["matched_count"] or 0)
    item["status"] = _status_from_counts(row)
    item["apply_state"] = _apply_state_from_bounds(row)
    return item


def get_navigation_overview(changeset_id_or_db_path: str | Path, *, base_dir: str | Path | None = None) -> list[dict[str, Any]]:
    db_path = _resolve_changeset_db_path(changeset_id_or_db_path, base_dir=base_dir)
    category_rows: dict[str, dict[str, Any]] = {}
    category_map = {
        OBJECT_TYPES["dimension"].lower(): "dimensions",
        OBJECT_TYPES["hierarchy"].lower(): "hierarchies",
        OBJECT_TYPES["subset"].lower(): "subsets",
        OBJECT_TYPES["element"].lower(): "elements",
        OBJECT_TYPES["edge"].lower(): "edges",
        OBJECT_TYPES["cube"].lower(): "cubes",
        OBJECT_TYPES["rule"].lower(): "rules",
        OBJECT_TYPES["mdx_view"].lower(): "views",
        OBJECT_TYPES["native_view"].lower(): "views",
        OBJECT_TYPES["process"].lower(): "processes",
        OBJECT_TYPES["chore"].lower(): "chores",
    }
    sql = """
        SELECT
            object_type,
            COUNT(*) AS matched_count,
            MIN(apply) AS min_apply,
            MAX(apply) AS max_apply,
            SUM(CASE WHEN lower(change_type) = 'remove' THEN 1 ELSE 0 END) AS remove_count,
            SUM(CASE WHEN lower(change_type) = 'modify' THEN 1 ELSE 0 END) AS modify_count,
            SUM(CASE WHEN lower(change_type) = 'add' THEN 1 ELSE 0 END) AS add_count
        FROM changes
        GROUP BY object_type
    """
    with _readonly_connection(db_path) as conn:
        for row in conn.execute(sql).fetchall():
            category = category_map.get(str(row["object_type"] or "").strip().lower())
            if not category:
                continue
            existing = category_rows.get(category)
            if existing is None:
                category_rows[category] = {
                    "matched_count": int(row["matched_count"] or 0),
                    "min_apply": int(row["min_apply"] or 0),
                    "max_apply": int(row["max_apply"] or 0),
                    "remove_count": int(row["remove_count"] or 0),
                    "modify_count": int(row["modify_count"] or 0),
                    "add_count": int(row["add_count"] or 0),
                }
                continue
            existing["matched_count"] += int(row["matched_count"] or 0)
            existing["min_apply"] = min(existing["min_apply"], int(row["min_apply"] or 0))
            existing["max_apply"] = max(existing["max_apply"], int(row["max_apply"] or 0))
            existing["remove_count"] += int(row["remove_count"] or 0)
            existing["modify_count"] += int(row["modify_count"] or 0)
            existing["add_count"] += int(row["add_count"] or 0)
    return [
        _build_overview_item(category, category_rows.get(category))
        for category in ("dimensions", "hierarchies", "subsets", "elements", "edges", "cubes", "rules", "views", "processes", "chores")
    ]


def _stage_for_scope(scope_type: str, path: dict[str, str]) -> dict[str, Any]:
    dimension = path.get("dimension", "")
    hierarchy = path.get("hierarchy", "")
    cube = path.get("cube", "")

    if scope_type == "dimensions":
        return {
            "level_name": "dimension",
            "group_expr": "dim_name",
            "where": [f"object_type = '{OBJECT_TYPES['dimension']}'", "dim_name IS NOT NULL", "dim_name != ''"],
            "params": [],
        }
    if scope_type == "hierarchies":
        if not dimension:
            return {
                "level_name": "dimension",
                "group_expr": "dim_name",
                "where": [f"object_type = '{OBJECT_TYPES['hierarchy']}'", "dim_name IS NOT NULL", "dim_name != ''"],
                "params": [],
            }
        return {
            "level_name": "hierarchy",
            "group_expr": "hier_name",
            "where": [f"object_type = '{OBJECT_TYPES['hierarchy']}'", "dim_name = ?", "hier_name IS NOT NULL", "hier_name != ''"],
            "params": [dimension],
        }
    if scope_type in {"subsets", "elements"}:
        object_type = {
            "subsets": OBJECT_TYPES["subset"],
            "elements": OBJECT_TYPES["element"],
        }[scope_type]
        leaf_name = {"subsets": "subset", "elements": "element"}[scope_type]
        if not dimension:
            return {
                "level_name": "dimension",
                "group_expr": "dim_name",
                "where": ["object_type = ?", "dim_name IS NOT NULL", "dim_name != ''"],
                "params": [object_type],
            }
        if not hierarchy:
            return {
                "level_name": "hierarchy",
                "group_expr": "hier_name",
                "where": ["object_type = ?", "dim_name = ?", "hier_name IS NOT NULL", "hier_name != ''"],
                "params": [object_type, dimension],
            }
        return {
            "level_name": leaf_name,
            "group_expr": "object_name",
            "where": ["object_type = ?", "dim_name = ?", "hier_name = ?", "object_name IS NOT NULL", "object_name != ''"],
            "params": [object_type, dimension, hierarchy],
        }
    if scope_type == "edges":
        edge_group_expr = (
            "CASE "
            "WHEN COALESCE(json_extract(body_json, '$.source_name'), '') = '' AND COALESCE(json_extract(body_json, '$.target_name'), '') = '' THEN object_name "
            "WHEN COALESCE(json_extract(body_json, '$.source_name'), '') = '' THEN json_extract(body_json, '$.target_name') "
            "WHEN COALESCE(json_extract(body_json, '$.target_name'), '') = '' THEN json_extract(body_json, '$.source_name') "
            "ELSE json_extract(body_json, '$.source_name') || ' -> ' || json_extract(body_json, '$.target_name') "
            "END"
        )
        if not dimension:
            return {
                "level_name": "dimension",
                "group_expr": "dim_name",
                "where": ["object_type = ?", "dim_name IS NOT NULL", "dim_name != ''"],
                "params": [OBJECT_TYPES["edge"]],
            }
        if not hierarchy:
            return {
                "level_name": "hierarchy",
                "group_expr": "hier_name",
                "where": ["object_type = ?", "dim_name = ?", "hier_name IS NOT NULL", "hier_name != ''"],
                "params": [OBJECT_TYPES["edge"], dimension],
            }
        return {
            "level_name": "edge",
            "group_expr": edge_group_expr,
            "where": [
                "object_type = ?",
                "dim_name = ?",
                "hier_name = ?",
                f"{edge_group_expr} IS NOT NULL",
                f"{edge_group_expr} != ''",
            ],
            "params": [OBJECT_TYPES["edge"], dimension, hierarchy],
        }
    if scope_type == "cubes":
        return {
            "level_name": "cube",
            "group_expr": "cube_name",
            "where": [f"object_type = '{OBJECT_TYPES['cube']}'", "cube_name IS NOT NULL", "cube_name != ''"],
            "params": [],
        }
    if scope_type in {"views", "rules"}:
        leaf_name = "view" if scope_type == "views" else "rule"
        where: list[str] = []
        params: list[str] = []
        if scope_type == "views":
            where.append(f"object_type IN ({_sql_string_list(list(VIEW_OBJECT_TYPES))})")
        else:
            where.append("object_type = ?")
            params.append(OBJECT_TYPES["rule"])
        if not cube:
            return {
                "level_name": "cube",
                "group_expr": "cube_name",
                "where": where + ["cube_name IS NOT NULL", "cube_name != ''"],
                "params": params,
            }
        return {
            "level_name": leaf_name,
            "group_expr": "object_name",
            "where": where + ["cube_name = ?", "object_name IS NOT NULL", "object_name != ''"],
            "params": params + [cube],
        }
    if scope_type == "processes":
        return {
            "level_name": "process",
            "group_expr": "process_name",
            "where": [f"object_type = '{OBJECT_TYPES['process']}'", "process_name IS NOT NULL", "process_name != ''"],
            "params": [],
        }
    if scope_type == "chores":
        return {
            "level_name": "chore",
            "group_expr": "chore_name",
            "where": [f"object_type = '{OBJECT_TYPES['chore']}'", "chore_name IS NOT NULL", "chore_name != ''"],
            "params": [],
        }
    raise ValueError(f"Unsupported navigation scope_type: {scope_type}")


def _node_uri(scope_type: str, level_name: str, row_name: str, path: dict[str, str], *, has_children: bool) -> str:
    dimension = path.get("dimension", "")
    hierarchy = path.get("hierarchy", "")
    cube = path.get("cube", "")
    chore = path.get("chore", "")
    if scope_type == "dimensions":
        return f"Dimensions('{_tm1_quote(row_name)}')"
    if scope_type == "hierarchies":
        if level_name == "dimension":
            return f"Dimensions('{_tm1_quote(row_name)}')/Hierarchies('*')"
        return f"Dimensions('{_tm1_quote(dimension)}')/Hierarchies('{_tm1_quote(row_name)}')"
    if scope_type == "subsets":
        if level_name == "dimension":
            return f"Dimensions('{_tm1_quote(row_name)}')/Hierarchies('*')/Subsets('*')"
        if level_name == "hierarchy":
            return f"Dimensions('{_tm1_quote(dimension)}')/Hierarchies('{_tm1_quote(row_name)}')/Subsets('*')"
        return f"Dimensions('{_tm1_quote(dimension)}')/Hierarchies('{_tm1_quote(hierarchy)}')/Subsets('{_tm1_quote(row_name)}')"
    if scope_type == "elements":
        if level_name == "dimension":
            return f"Dimensions('{_tm1_quote(row_name)}')/Hierarchies('*')/Elements('*')"
        if level_name == "hierarchy":
            return f"Dimensions('{_tm1_quote(dimension)}')/Hierarchies('{_tm1_quote(row_name)}')/Elements('*')"
        return f"Dimensions('{_tm1_quote(dimension)}')/Hierarchies('{_tm1_quote(hierarchy)}')/Elements('{_tm1_quote(row_name)}')"
    if scope_type == "edges":
        if level_name == "dimension":
            return f"Dimensions('{_tm1_quote(row_name)}')/Hierarchies('*')/Edges('*'/'*')"
        if level_name == "hierarchy":
            return f"Dimensions('{_tm1_quote(dimension)}')/Hierarchies('{_tm1_quote(row_name)}')/Edges('*'/'*')"
        return f"Dimensions('{_tm1_quote(dimension)}')/Hierarchies('{_tm1_quote(hierarchy)}')/Edges('*'/'*')"
    if scope_type == "cubes":
        return f"Cubes('{_tm1_quote(row_name)}')"
    if scope_type == "views":
        if level_name == "cube":
            return f"Cubes('{_tm1_quote(row_name)}')/Views('*')"
        return f"Cubes('{_tm1_quote(cube)}')/Views('{_tm1_quote(row_name)}')"
    if scope_type == "rules":
        if level_name == "cube":
            return f"Cubes('{_tm1_quote(row_name)}')/Rules('*')"
        return f"Cubes('{_tm1_quote(cube)}')/Rules('{_tm1_quote(row_name)}')"
    if scope_type == "processes":
        return f"Processes('{_tm1_quote(row_name)}')"
    if scope_type == "chores":
        if level_name == "chore":
            return f"Chores('{_tm1_quote(row_name)}')" if not has_children else f"Chores('{_tm1_quote(row_name)}')/Tasks('*')"
        return f"Chores('{_tm1_quote(chore)}')/Tasks('{_tm1_quote(row_name)}')"
    return row_name


def _item_path(scope_type: str, level_name: str, row_name: str, path: dict[str, str]) -> dict[str, str]:
    base = dict(path)
    if scope_type in {"dimensions", "hierarchies", "subsets", "elements", "edges"}:
        if level_name == "dimension":
            return {"dimension": row_name}
        if level_name == "hierarchy":
            return {"dimension": base.get("dimension", ""), "hierarchy": row_name}
        return {"dimension": base.get("dimension", ""), "hierarchy": base.get("hierarchy", ""), level_name: row_name}
    if scope_type in {"cubes", "views", "rules"}:
        if level_name == "cube":
            return {"cube": row_name}
        return {"cube": base.get("cube", ""), level_name: row_name}
    if scope_type == "processes":
        return {"process": row_name}
    if scope_type == "chores":
        if level_name == "chore":
            return {"chore": row_name}
        return {"chore": base.get("chore", ""), "task": row_name}
    return dict(path)


def _leaf_payload(scope_type: str, level_name: str, row_name: str, path: dict[str, str], row: sqlite3.Row) -> dict[str, Any]:
    body = {}
    try:
        body = orjson.loads(row["sample_body_json"]) if row["sample_body_json"] else {}
    except Exception:
        body = {}
    payload = {"body": body}
    if scope_type == "dimensions":
        payload.update({"object_name": row_name})
    elif scope_type == "hierarchies" and level_name == "hierarchy":
        payload.update({"object_name": path.get("dimension", ""), "hierarchy_name": row_name})
    elif scope_type in {"subsets", "elements", "edges"} and level_name in {"subset", "element", "edge"}:
        payload.update({"object_name": path.get("dimension", ""), "hierarchy_name": path.get("hierarchy", "")})
        if level_name == "subset":
            payload["view_name"] = row_name
        elif level_name == "edge":
            source_name, target_name = _resolve_edge_names(row, body)
            payload["source_name"] = source_name
            payload["target_name"] = target_name
    elif scope_type == "cubes":
        payload.update({"object_name": row_name})
    elif scope_type == "views" and level_name == "view":
        payload.update({"object_name": path.get("cube", ""), "view_name": row_name})
    elif scope_type == "rules" and level_name == "rule":
        payload.update({"object_name": path.get("cube", "")})
    elif scope_type == "processes":
        payload.update({"object_name": row_name})
    elif scope_type == "chores":
        if level_name == "chore":
            payload.update({"object_name": row_name})
        elif level_name == "task":
            payload.update({"object_name": row_name, "chore_name": path.get("chore", "")})
    return payload


def _has_children(scope_type: str, level_name: str) -> bool:
    if scope_type == "hierarchies":
        return level_name == "dimension"
    if scope_type in {"subsets", "elements", "edges"}:
        return level_name in {"dimension", "hierarchy"}
    if scope_type in {"views", "rules"}:
        return level_name == "cube"
    return False


def get_navigation_items(
    changeset_id_or_db_path: str | Path,
    *,
    scope_type: str,
    path: dict[str, str],
    limit: int,
    offset: int,
    search: str = "",
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    db_path = _resolve_changeset_db_path(changeset_id_or_db_path, base_dir=base_dir)
    normalized_scope = normalize_scope_type(scope_type)
    stage = _append_search_clause(_stage_for_scope(normalized_scope, path), search)
    bounded_limit = max(1, min(int(limit or 50), 200))
    bounded_offset = max(0, int(offset or 0))
    fetch_limit = bounded_limit + 1
    with _readonly_connection(db_path) as conn:
        total_count = _execute_grouped_count_query(
            conn,
            group_expr=stage["group_expr"],
            where_clauses=stage["where"],
            params=stage["params"],
        )
        rows = _execute_grouped_query(
            conn,
            group_expr=stage["group_expr"],
            where_clauses=stage["where"],
            params=stage["params"],
            limit=fetch_limit,
            offset=bounded_offset,
        )
    has_more = len(rows) > bounded_limit
    rows = rows[:bounded_limit]
    page_count = (total_count + bounded_limit - 1) // bounded_limit if total_count > 0 else 0
    page = (bounded_offset // bounded_limit) + 1 if total_count > 0 else 0
    items: list[dict[str, Any]] = []
    for row in rows:
        group_name = str(row["group_name"] or "").strip()
        if not group_name:
            continue
        level_name = stage["level_name"]
        has_children = _has_children(normalized_scope, level_name)
        item_name = group_name
        item_uri = _node_uri(normalized_scope, level_name, group_name, path, has_children=has_children)
        item_path = _item_path(normalized_scope, level_name, group_name, path)
        if normalized_scope == "edges" and level_name == "edge":
            body = {}
            try:
                body = orjson.loads(row["sample_body_json"]) if row["sample_body_json"] else {}
            except Exception:
                body = {}
            source_name, target_name = _resolve_edge_names(row, body)
            item_name = _format_edge_label(source_name, target_name, fallback=group_name)
            if source_name or target_name:
                item_uri = (
                    f"Dimensions('{_tm1_quote(path.get('dimension', ''))}')/"
                    f"Hierarchies('{_tm1_quote(path.get('hierarchy', ''))}')/"
                    f"Edges('{_tm1_quote(source_name)}'/'{_tm1_quote(target_name)}')"
                )
        item = {
            "name": item_name,
            "uri": item_uri,
            "object_type": level_name,
            "status": _status_from_counts(row),
            "apply_state": _apply_state_from_bounds(row),
            "has_children": has_children,
            "matched_count": int(row["matched_count"] or 0),
            "path": item_path,
            "level_name": level_name,
        }
        if not has_children:
            item.update(_leaf_payload(normalized_scope, level_name, item_name, path, row))
        items.append(item)
    return {
        "scope_type": normalized_scope,
        "level_name": stage["level_name"],
        "path": path,
        "search": stage.get("search", ""),
        "limit": bounded_limit,
        "page_size": bounded_limit,
        "offset": bounded_offset,
        "page": page,
        "page_count": page_count,
        "total_count": total_count,
        "has_more": has_more,
        "items": items,
    }
