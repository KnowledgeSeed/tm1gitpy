import copy
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, TypeVar, Union

import yaml

from tm1_git_py.model import Chore, Cube, Dimension, Hierarchy, MDXView, Process, Subset, hierarchy, subset, \
    mdxview, Element, Rule, Edge

logger = logging.getLogger(__name__)


T = TypeVar('T', Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore, Element, Edge, Rule)

_CHILD_RELATIONS: dict[type, list[str]] = {
    Dimension: ["hierarchies"],
    Hierarchy: ["subsets"],
    Cube: ["views"],
}

OBJECT_PRECEDENCE = {
    'Dimension': 0,
    'Hierarchy': 1,
    'Subset': 2,
    'Element': 3,
    'Edge': 4,
    'Cube': 5,
    'MDXView': 6,
    'Rule': 7,
    'Process': 8,
    'Chore': 9
}
DELETE_OBJECT_PRECEDENCE = {
    'MDXView': 0,
    'Rule': 1,
    'Cube': 2,
    'Edge': 3,
    'Element': 4,
    'Subset': 5,
    'Hierarchy': 6,
    'Dimension': 7,
    'Chore': 8,
    'Process': 9
}


class ChangeType(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    MODIFY = "modify"


class ObjectType(str, Enum):
    CUBE = "Cube"
    CHORE = "Chore"
    MDX_VIEW = "MDXView"
    DIMENSION = "Dimension"
    HIERARCHY = "Hierarchy"
    SUBSET = "Subset"
    ELEMENT = "Element"
    EDGE = "Edge"
    RULE = "Rule"
    PROCESS = "Process"
    TI_PROCESS = "Process"

    @classmethod
    def from_raw(cls, value: Optional[str]) -> "ObjectType":
        normalized = (value or "").strip().lower()
        aliases = {
            "cube": cls.CUBE,
            "chore": cls.CHORE,
            "mdxview": cls.MDX_VIEW,
            "dimension": cls.DIMENSION,
            "hierarchy": cls.HIERARCHY,
            "subset": cls.SUBSET,
            "element": cls.ELEMENT,
            "edge": cls.EDGE,
            "rule": cls.RULE,
            "process": cls.PROCESS,
            "tiprocess": cls.PROCESS,
        }
        if normalized not in aliases:
            raise ValueError(f"Unsupported object type '{value}'.")
        return aliases[normalized]

    @classmethod
    def from_object(cls, obj: Any) -> "ObjectType":
        return cls.from_raw(obj.__class__.__name__)


ChangesetBody = Union[Cube, Chore, MDXView, Dimension, Hierarchy, Subset, Element, Edge, Rule, Process]

OBJECT_TYPE_TO_CLASS: dict[ObjectType, type] = {
    ObjectType.CUBE: Cube,
    ObjectType.CHORE: Chore,
    ObjectType.MDX_VIEW: MDXView,
    ObjectType.DIMENSION: Dimension,
    ObjectType.HIERARCHY: Hierarchy,
    ObjectType.SUBSET: Subset,
    ObjectType.ELEMENT: Element,
    ObjectType.EDGE: Edge,
    ObjectType.RULE: Rule,
    ObjectType.PROCESS: Process,
}


@dataclass
class Change:
    """A single change entry describing one add / delete / update operation."""

    change_type: ChangeType
    object_type: ObjectType
    source_path: str
    body: ChangesetBody


@dataclass
class Changeset:
    """A collection of changes to apply."""

    changes: list[Change]

    def __init__(self):
        self.changes: list[Change] = []
        self.last_execution_id: str = '0'

        self.errors: dict[str, list[Any]] = {}
        self.sort()

    def has_changes(self) -> bool:
        return bool(self.changes)

    def apply(
            self,
            tm1_service,
            *,
            status_dir: Optional[Union[str, Path]] = None,
            execution_id: Optional[str] = None,
            changeset_name: Optional[str] = None,
            fail_fast: bool = True,
            **kwargs
    ) -> tuple[bool, Union[list, None]]:
        from tm1_git_py.apply import apply as apply_changeset

        return apply_changeset(
            changeset=self,
            tm1_service=tm1_service,
            status_dir=status_dir,
            execution_id=execution_id,
            changeset_name=changeset_name,
            fail_fast=fail_fast,
            **kwargs
        )


    def sort(self):
        if self.has_changes():
            def _change_key(change: Change) -> tuple[int, tuple[int, str]]:
                body = change.body
                change_path = change.source_path or getattr(body, "source_path", "") or ""
                if not change_path:
                    path_key = (99, "")
                elif change.change_type == ChangeType.REMOVE:
                    path_key = _source_path_sort_key(body, delete_precedence=True)
                else:
                    path_key = _source_path_sort_key(body)

                type_rank = {
                    ChangeType.REMOVE: 0,
                    ChangeType.ADD: 1,
                    ChangeType.MODIFY: 2,
                }.get(change.change_type, 99)
                return type_rank, path_key

            self.changes.sort(key=_change_key)


    def export(self, file_path: Union[str, Path]) -> None:
        """Export changeset in fixture-compatible flat YAML format."""

        self.sort()

        export_entries = []
        summary = {"add": 0, "remove": 0, "modify": 0}
        for change in self.changes:
            change_type = change.change_type
            if hasattr(change_type, "value"):
                change_type = change.change_type.value
            export_entries.append({
                "change_type": change_type,
                "object_type": change.object_type.value,
                "source_path": change.source_path,
                "body": _serialize_change_body(change),
            })
            summary[change_type] += 1

        output_path = Path(file_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "changeset_name": None,
            "summary": summary,
            "changes": export_entries
        }
        output_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------------

def normalize_source_path(source_path: str) -> str:
    if not source_path:
        return ""

    normalized = source_path.replace("\\", "/").lstrip("/")
    if normalized.endswith(".json"):
        normalized = normalized[:-5]

    return normalized


def _source_path_sort_key(s: Union[T, dict[T, Any]], delete_precedence = False):
    if not isinstance(s, (Cube, MDXView, Dimension, Hierarchy, Subset, Chore, Process, Element, Edge, Rule)):
        raise ValueError(f"Cannot sort object type for source path '{s}'")

    object_type = s.__class__.__name__
    source_path = (getattr(s, "source_path", "") or "").replace("\\", "/").lstrip("/")
    parent_path = source_path.rsplit("/", 1)[0] if "/" in source_path else ""

    object_name = getattr(s, "name", None)
    if object_name is None and isinstance(s, Rule):
        object_name = _rule_name_from_area(getattr(s, "area", ""))
    elif object_name is None and isinstance(s, Edge):
        object_name = f"{getattr(s, 'parent', '')}:{getattr(s, 'name', '')}"
    elif object_name is None:
        object_name = source_path.rsplit("/", 1)[-1]

    precedence_map = DELETE_OBJECT_PRECEDENCE if delete_precedence else OBJECT_PRECEDENCE
    return (
        precedence_map.get(object_type, 99),
        parent_path,
        str(object_name),
    )


def _iter_changeset_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return fixture-style changeset entries from payload['changes']."""
    changes = payload.get("changes", [])
    if not isinstance(changes, list):
        raise ValueError("changeset payload must contain a list under 'changes'")
    return [entry for entry in changes if isinstance(entry, dict)]


def _path_stem(source_path: Optional[str]) -> str:
    if not source_path:
        return ""
    normalized = source_path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return name[:-5] if name.endswith(".json") else name


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().strip(",").lower() in {"1", "true", "yes"}
    return bool(value)


def _rule_name_from_area(area: str) -> str:
    if not area:
        return "default"
    match = re.match(r"\[([^\]]+)\]", area.strip())
    if match:
        return match.group(1)
    return "default"


def _remove_body_name(body: Any) -> str:
    if isinstance(body, Rule):
        return _rule_name_from_area(body.area)
    if isinstance(body, Edge):
        return f"{body.parent}:{body.name}"
    return getattr(body, "name", "")


def _serialize_change_body(change: Change) -> dict[str, Any]:
    body = change.body
    if change.change_type == ChangeType.REMOVE:
        return {"name": _remove_body_name(body)}

    if isinstance(body, MDXView):
        return {
            "name": body.name,
            "mdx": body.mdx
        }

    if isinstance(body, Rule):
        return {
            "name": _rule_name_from_area(body.area),
            "rule": body.full_statement
        }

    if isinstance(body, Cube):
        return {
            "name": body.name,
            "dimensions": [getattr(d, "source_path", f"dimensions/{d.name}.json") for d in body.dimensions]
        }

    if isinstance(body, Subset):
        return {
            "name": body.name,
            "expression": body.expression
        }

    if isinstance(body, Edge):
        return {
            "parent_name": body.parent,
            "component_name": body.name,
            "weight": body.weight
        }

    if isinstance(body, Element):
        return {
            "name": body.name,
            "type": body.type
        }

    if isinstance(body, Hierarchy):
        return {
            "name": body.name
        }

    if isinstance(body, Dimension):
        return {
            "name": body.name,
            "hierarchies": [getattr(h, "source_path", f"dimensions/{body.name}.hierarchies/{h.name}.json") for h in body.hierarchies],
            "default_hierarchy": getattr(body.defaultHierarchy, "source_path",
                                         f"dimensions/{body.name}.hierarchies/{body.defaultHierarchy.name}.json")
        }

    if isinstance(body, Process):
        return {
            "name": body.name,
            "has_security_access": body.hasSecurityAccess,
            "data_source": body.datasource if isinstance(body.datasource, dict) else {"type": body.datasource or "None"},
            "parameters": body.parameters,
            "variables": body.variables,
            "prolog": body.ti.prolog_procedure,
            "data": body.ti.data_procedure,
            "metadata": body.ti.metadata_procedure,
            "epilog": body.ti.epilog_procedure,
        }

    if isinstance(body, Chore):
        start_date = body.start_time.split("T")[0] if isinstance(body.start_time, str) and "T" in body.start_time else body.start_time
        return {
            "name": body.name,
            "active": body.active,
            "start_date": start_date,
            "dst_sensitive": body.dst_sensitive,
            "execution_mode": body.execution_mode,
            "frequency": body.frequency,
            "tasks": [f"processes/{task.process_name}.json" for task in body.tasks]
        }

    if hasattr(body, "to_dict"):
        return copy.deepcopy(body.to_dict())
    raise ValueError(f"Unsupported change body type: {type(body).__name__}")


def _normalize_body_payload(
        object_type: ObjectType,
        payload: dict[str, Any],
        source_path: str
) -> dict[str, Any]:
    normalized = copy.deepcopy(payload or {})

    if object_type == ObjectType.EDGE:
        if "parent_name" in normalized and "parentName" not in normalized:
            normalized["parentName"] = normalized["parent_name"]
        if "component_name" in normalized and "componentName" not in normalized:
            normalized["componentName"] = normalized["component_name"]

    if object_type == ObjectType.RULE:
        if "rule" in normalized and "statement" not in normalized and "full_statement" not in normalized:
            normalized["statement"] = normalized["rule"]
        if "area" not in normalized and "Area" not in normalized:
            rule_name = normalized.get("name") or "default"
            normalized["area"] = f"[{rule_name}]"

    if object_type == ObjectType.DIMENSION:
        hierarchies = normalized.get("hierarchies")
        if isinstance(hierarchies, list) and hierarchies and isinstance(hierarchies[0], str):
            normalized["hierarchies"] = [{"name": _path_stem(path)} for path in hierarchies]
        default_hierarchy = normalized.get("default_hierarchy") or normalized.get("defaultHierarchy")
        if isinstance(default_hierarchy, str):
            normalized["defaultHierarchy"] = {"name": _path_stem(default_hierarchy)}

    if object_type == ObjectType.CUBE:
        dimensions = normalized.get("dimensions")
        if isinstance(dimensions, list) and dimensions and isinstance(dimensions[0], str):
            normalized["dimensions"] = [{"name": _path_stem(path)} for path in dimensions]

    if object_type == ObjectType.PROCESS:
        data_source = normalized.get("data_source")
        if data_source is not None and "datasource" not in normalized:
            normalized["datasource"] = data_source

        if "ti" not in normalized:
            normalized["ti"] = {
                "prolog_procedure": normalized.get("prolog", ""),
                "metadata_procedure": normalized.get("metadata", ""),
                "data_procedure": normalized.get("data", ""),
                "epilog_procedure": normalized.get("epilog", ""),
            }
        if "code_link" not in normalized:
            normalized["code_link"] = f"{_path_stem(source_path)}.ti"

    if object_type == ObjectType.CHORE:
        if "start_time" not in normalized:
            normalized["start_time"] = normalized.get("start_date")
        if "dst_sensitive" in normalized:
            normalized["dst_sensitive"] = _as_bool(normalized.get("dst_sensitive"))
        if "active" in normalized:
            normalized["active"] = _as_bool(normalized.get("active"))
        tasks = normalized.get("tasks")
        if isinstance(tasks, list) and tasks and isinstance(tasks[0], str):
            normalized["tasks"] = [
                {"process_name": _path_stem(task_path), "parameters": []}
                for task_path in tasks
            ]

    return normalized


# --------------------------------------------------------------------------------
# Import changeset function & helpers
# --------------------------------------------------------------------------------

def import_changeset(changeset_file: Union[str, Path]) -> Changeset:
    """
    Build a Changeset from fixture-style YAML/JSON:
    changes:
      - change_type: add|remove|modify
        object_type: <ObjectType>
        source_path: <path>
        body: <object payload>
    """
    try:
        payload = _load_changeset_payload(changeset_file)
    except Exception as exc:
        logger.error("Failed to load changeset payload from '%s': %s", changeset_file, exc)
        raise

    entries = _iter_changeset_entries(payload)

    changeset = Changeset()

    for entry in entries:
        if not isinstance(entry, dict):
            logger.warning("Skipping malformed changeset entry: %s", entry)
            continue

        change_type = entry.get("change_type")
        object_type = ObjectType.from_raw(entry.get("object_type"))
        source_path = (entry.get("source_path") or "").replace("\\", "/")
        body_payload = entry.get("body") or {}
        if not source_path:
            raise ValueError(f"Missing source_path in changeset entry {entry}")

        normalized_payload = _normalize_body_payload(object_type, body_payload, source_path)
        body_object = _deserialize_object_from_payload(object_type.value, normalized_payload, source_path)
        if body_object is None:
            raise ValueError(f"Failed to deserialize entry {entry}")

        changeset.changes.append(
            Change(
                change_type=change_type,
                object_type=object_type,
                source_path=source_path,
                body=body_object
            )
        )

    changeset.sort()
    return changeset


def _load_changeset_payload(changeset_file) -> dict[str, Any]:
    if hasattr(changeset_file, "read"):
        content = changeset_file.read()
        if not isinstance(content, str):
            content = content.decode("utf-8")
        try:
            return json.loads(content)
        except Exception:
            return yaml.safe_load(content)

    with open(changeset_file, "r", encoding="utf-8") as handle:
        content = handle.read()
        try:
            return json.loads(content)
        except Exception:
            return yaml.safe_load(content)


def _build_dimension_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> Dimension:
    return Dimension.from_dict(payload, source_path=source_path)


def _build_hierarchy_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> Hierarchy:
    dimension_name, _ = hierarchy._hierarchy_context_from_path(source_path)
    if not dimension_name:
        raise ValueError("Hierarchy payload missing dimension context.")
    return Hierarchy.from_dict(payload, source_path=source_path, dimension_name=dimension_name)


def _build_subset_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> Subset:
    dimension_name, hierarchy_name = subset._subset_context_from_path(source_path)
    if not dimension_name or not hierarchy_name:
        raise ValueError("Subset payload missing dimension or hierarchy context.")
    return Subset.from_dict(payload, source_path=source_path,
                            dimension_name=dimension_name, hierarchy_name=hierarchy_name)


def _build_cube_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> Cube:
    return Cube.from_dict(payload, source_path=source_path)


def _build_mdx_view_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> MDXView:
    cube_name, _ = mdxview._view_context_from_path(source_path)
    if not source_path and not cube_name:
        raise ValueError("MDXView payload missing cube context.")
    return MDXView.from_dict(payload, source_path=source_path, cube_name=cube_name)


def _build_process_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> Process:
    return Process.from_dict(payload, source_path=source_path)


def _build_chore_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> Chore:
    return Chore.from_dict(payload, source_path=source_path)

def _build_element_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> Element:
    return Element.from_dict(payload, source_path=source_path)


def _build_edge_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> Edge:
    return Edge.from_dict(payload, source_path=source_path)


def _build_rule_from_payload(payload: dict[str, Any], source_path: Optional[str]) -> Rule:
    cube_name = None
    if source_path and source_path.startswith("cubes/"):
        cube_name = source_path.split("/", 1)[1].split(".", 1)[0]
    normalized_payload = dict(payload)
    if "rule" in normalized_payload and "statement" not in normalized_payload and "full_statement" not in normalized_payload:
        normalized_payload["statement"] = normalized_payload["rule"]
    return Rule.from_dict(normalized_payload, source_path=source_path, cube_name=cube_name)


_OBJECT_BUILDERS: dict[str, Any] = {
    "Dimension": _build_dimension_from_payload,
    "Hierarchy": _build_hierarchy_from_payload,
    "Subset": _build_subset_from_payload,
    "Cube": _build_cube_from_payload,
    "MDXView": _build_mdx_view_from_payload,
    "Process": _build_process_from_payload,
    "Chore": _build_chore_from_payload,
    "Element": _build_element_from_payload,
    "Edge": _build_edge_from_payload,
    "Rule": _build_rule_from_payload,
    "TIProcess": _build_process_from_payload,
}


def _deserialize_object_from_payload(object_type: Optional[str],
                                     payload: Optional[dict[str, Any]],
                                     source_path: Optional[str]) -> Optional[Any]:
    if not payload or not object_type:
        return None
    builder = _OBJECT_BUILDERS.get(object_type)
    if builder is None:
        raise ValueError(f"Unsupported object type '{object_type}' in changeset import.")
    return builder(payload, source_path)


def _deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
