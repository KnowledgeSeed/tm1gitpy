import copy
import json
import logging
import re
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator, MutableSequence, Optional, Union

import orjson
import yaml

from tm1_git_py.changeset_store import ChangesetStore
from tm1_git_py.model import Chore, Cube, Dimension, Hierarchy, MDXView, NativeView, Process, Subset, hierarchy, subset, \
    mdxview, Element, Rule, Edge

logger = logging.getLogger(__name__)

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
    'NativeView': 7,
    'Rule': 8,
    'Process': 9,
    'Chore': 10
}
DELETE_OBJECT_PRECEDENCE = {
    'NativeView': 0,
    'MDXView': 1,
    'Rule': 2,
    'Cube': 3,
    'Edge': 4,
    'Element': 5,
    'Subset': 6,
    'Hierarchy': 7,
    'Dimension': 8,
    'Chore': 9,
    'Process': 10
}

_URI_PATTERNS = {
    "dimension": re.compile(r"^Dimensions\('([^']+)'\)$", flags=re.IGNORECASE),
    "hierarchy": re.compile(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)$", flags=re.IGNORECASE),
    "element": re.compile(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)/Elements\('([^']+)'\)$", flags=re.IGNORECASE),
    "subset": re.compile(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)/Subsets\('([^']+)'\)$", flags=re.IGNORECASE),
    "edge": re.compile(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)/Edges\('([^']+)'\)$", flags=re.IGNORECASE),
    "cube": re.compile(r"^Cubes\('([^']+)'\)$", flags=re.IGNORECASE),
    "view": re.compile(r"^Cubes\('([^']+)'\)/Views\('([^']+)'\)$", flags=re.IGNORECASE),
    "rule": re.compile(r"^Cubes\('([^']+)'\)/Rules\('([^']+)'\)$", flags=re.IGNORECASE),
    "process": re.compile(r"^Processes\('([^']+)'\)$", flags=re.IGNORECASE),
    "chore": re.compile(r"^Chores\('([^']+)'\)$", flags=re.IGNORECASE),
    "task": re.compile(r"^Chores\('([^']+)'\)/Tasks\('([^']+)'\)$", flags=re.IGNORECASE),
}


class ChangeType(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    MODIFY = "modify"

    @classmethod
    def from_raw(cls, value: Any) -> "ChangeType":
        if isinstance(value, cls):
            return value

        normalized = (str(value or "")).strip().lower()
        aliases = {
            "add": cls.ADD,
            "remove": cls.REMOVE,
            "modify": cls.MODIFY,
        }
        if normalized not in aliases:
            raise ValueError(f"Unsupported change type '{value}'.")
        return aliases[normalized]


class ObjectType(str, Enum):
    CUBE = "Cube"
    CHORE = "Chore"
    MDX_VIEW = "MDXView"
    NATIVE_VIEW = "NativeView"
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
            "nativeview": cls.NATIVE_VIEW,
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


ChangesetBody = Union[Cube, Chore, MDXView, NativeView, Dimension, Hierarchy, Subset, Element, Edge, Rule, Process]

OBJECT_TYPE_TO_CLASS: dict[ObjectType, type] = {
    ObjectType.CUBE: Cube,
    ObjectType.CHORE: Chore,
    ObjectType.MDX_VIEW: MDXView,
    ObjectType.NATIVE_VIEW: NativeView,
    ObjectType.DIMENSION: Dimension,
    ObjectType.HIERARCHY: Hierarchy,
    ObjectType.SUBSET: Subset,
    ObjectType.ELEMENT: Element,
    ObjectType.EDGE: Edge,
    ObjectType.RULE: Rule,
    ObjectType.PROCESS: Process,
}


def _generate_changeset_id() -> str:
    return datetime.utcnow().strftime("%Y%m%d%H%M%S")


def _extract_uri_index_fields(uri: str) -> dict[str, Optional[str]]:
    fields = {
        "dim_name": None,
        "hier_name": None,
        "object_name": None,
        "cube_name": None,
        "process_name": None,
        "chore_name": None,
    }
    value = (uri or "").strip()
    for key, pattern in _URI_PATTERNS.items():
        match = pattern.fullmatch(value)
        if not match:
            continue
        groups = match.groups()
        if key == "dimension":
            fields["dim_name"] = groups[0]
            fields["object_name"] = groups[0]
        elif key == "hierarchy":
            fields["dim_name"] = groups[0]
            fields["hier_name"] = groups[1]
            fields["object_name"] = groups[1]
        elif key in {"element", "subset"}:
            fields["dim_name"] = groups[0]
            fields["hier_name"] = groups[1]
            fields["object_name"] = groups[2]
        elif key == "edge":
            fields["dim_name"] = groups[0]
            fields["hier_name"] = groups[1]
            fields["object_name"] = groups[2]
        elif key in {"cube", "view", "rule"}:
            fields["cube_name"] = groups[0]
            fields["object_name"] = groups[0] if key == "cube" else groups[1]
        elif key == "process":
            fields["process_name"] = groups[0]
            fields["object_name"] = groups[0]
        elif key == "chore":
            fields["chore_name"] = groups[0]
            fields["object_name"] = groups[0]
        elif key == "task":
            fields["chore_name"] = groups[0]
            fields["object_name"] = groups[1]
        return fields
    return fields


def _change_sort_fields(change: "Change") -> dict[str, Any]:
    body = change.body
    uri_sort_key = change.uri or ""
    object_type = body.__class__.__name__
    precedence_map = DELETE_OBJECT_PRECEDENCE if change.change_type == ChangeType.REMOVE else OBJECT_PRECEDENCE
    type_rank = {
        ChangeType.REMOVE: 0,
        ChangeType.ADD: 1,
        ChangeType.MODIFY: 2,
    }.get(change.change_type, 99)

    body_name = getattr(body, "name", None)
    if body_name is None and isinstance(body, Rule):
        body_name = _rule_name_from_area(getattr(body, "area", ""))
    if body_name is None:
        body_name = uri_sort_key

    return {
        "type_rank": int(type_rank),
        "precedence_rank": int(precedence_map.get(object_type, 99)),
        "body_name": str(body_name),
    }


@dataclass
class Change:
    """A single change entry describing one add / remove / modify operation."""

    change_type: ChangeType
    object_type: ObjectType
    uri: str
    body: ChangesetBody
    apply: bool = True

    def __post_init__(self):
        self.change_type = ChangeType.from_raw(self.change_type)
        self.object_type = ObjectType.from_raw(self.object_type)


class ChangesetSequence(MutableSequence[Change]):
    def __init__(self, owner: "Changeset"):
        self._owner = owner

    def __len__(self) -> int:
        return self._owner._get_store().count_rows()

    def __iter__(self) -> Iterator[Change]:
        store = self._owner._get_store()
        for row in store.iter_rows(sorted_order=True):
            yield self._owner._change_from_store_row(row)

    def __getitem__(self, index: int) -> Change:
        if not isinstance(index, int):
            raise TypeError("ChangesetSequence supports integer indexes only.")
        if index < 0:
            index = len(self) + index
        row = self._owner._get_store().row_at(index, sorted_order=True)
        if row is None:
            raise IndexError("ChangesetSequence index out of range.")
        return self._owner._change_from_store_row(row)

    def __setitem__(self, index: int, value: Change) -> None:
        raise NotImplementedError("Indexed assignment is not supported for streaming ChangesetSequence.")

    def __delitem__(self, index: int) -> None:
        raise NotImplementedError("Indexed deletion is not supported for streaming ChangesetSequence.")

    def insert(self, index: int, value: Change) -> None:
        raise NotImplementedError("Insert is not supported for streaming ChangesetSequence.")

    def append(self, value: Change) -> None:
        self._owner._append_change(value)

    def extend(self, values: Iterable[Change]) -> None:
        self._owner._append_changes(values)


@dataclass
class Changeset:
    """A collection of changes to apply."""

    _changes: ChangesetSequence

    def __init__(
            self,
            changeset_id: Optional[str] = None,
            *,
            require_existing_store: bool = False
    ):
        self.changeset_id: str = (changeset_id or "").strip() or _generate_changeset_id()
        self.last_execution_id: str = '0'
        self._require_existing_store = bool(require_existing_store)

        self.errors: dict[str, list[Any]] = {}
        self._store: Optional[ChangesetStore] = None
        self._reset_store_on_first_open = not self._require_existing_store
        self._changes = ChangesetSequence(self)

    @classmethod
    def from_changeset_id(cls, changeset_id: str) -> "Changeset":
        store_path = ChangesetStore.path_for(changeset_id=changeset_id)
        if not store_path.exists():
            raise FileNotFoundError(f"Changeset sqlite not found for id '{changeset_id}': {store_path}")
        changeset = cls(
            changeset_id=changeset_id,
            require_existing_store=True,
        )
        # Fail fast if schema is unavailable/corrupt.
        _ = len(changeset.changes)
        return changeset

    @property
    def changes(self) -> ChangesetSequence:
        return self._changes

    @changes.setter
    def changes(self, values: Iterable[Change]) -> None:
        self._replace_changes(values)

    @property
    def sqlite_path(self) -> Path:
        return self._get_store().db_path

    def _get_store(self) -> ChangesetStore:
        if self._store is None:
            self._store = ChangesetStore(
                changeset_id=self.changeset_id,
                require_exists=self._require_existing_store,
            )
            if self._reset_store_on_first_open:
                # Constructor semantics: a new Changeset starts empty. Use from_changeset_id
                # when callers explicitly want to attach to an existing sqlite store.
                self._store.clear()
                self._reset_store_on_first_open = False
        return self._store

    def _row_from_change(self, change: Change, *, seq: int) -> dict[str, Any]:
        indexed = _extract_uri_index_fields(change.uri)
        return {
            "seq": seq,
            "change_type": change.change_type.value,
            "object_type": change.object_type.value,
            "uri": change.uri,
            "apply": change.apply,
            "body_json": orjson.dumps(_serialize_change_body(change)).decode("utf-8"),
            **indexed,
            **_change_sort_fields(change),
        }

    def _replace_changes(self, values: Iterable[Change]) -> None:
        rows = [self._row_from_change(change, seq=seq) for seq, change in enumerate(values)]
        self._get_store().replace_rows(rows)

    def _append_changes(self, values: Iterable[Change]) -> None:
        rows = [self._row_from_change(change, seq=0) for change in values]
        if not rows:
            return
        self._get_store().append_rows(rows)

    def _append_change(self, value: Change) -> None:
        self._append_changes([value])

    def _change_from_store_row(self, row: Any) -> Change:
        object_type = ObjectType.from_raw(row["object_type"])
        body_payload = orjson.loads(row["body_json"])
        normalized_payload = _normalize_body_payload(object_type, body_payload, row["uri"])
        body_object = _deserialize_object_from_payload(object_type.value, normalized_payload, row["uri"])
        return Change(
            change_type=ChangeType.from_raw(row["change_type"]),
            object_type=object_type,
            uri=row["uri"],
            body=body_object,
            apply=bool(row["apply"]),
        )

    def query(
            self,
            filter_rules: Optional[list[str]] = None,
            from_: int = 0,
            to: Optional[int] = None,
            *,
            apply_only: Optional[bool] = None,
            **legacy_kwargs: Any
    ) -> list[Change]:
        if filter_rules is None and "rules" in legacy_kwargs:
            filter_rules = legacy_kwargs["rules"]
        if "offset" in legacy_kwargs:
            from_ = int(legacy_kwargs["offset"])
        if "limit" in legacy_kwargs:
            limit_val = legacy_kwargs["limit"]
            to = None if limit_val is None else int(limit_val)

        if from_ < 0:
            raise ValueError("from must be >= 0")
        if to is not None and to < 0:
            raise ValueError("to must be >= 0")

        rows = self._get_store().query_rows(
            filter_rules=filter_rules,
            offset=from_,
            limit=to,
            apply_only=apply_only,
            sorted_order=True,
        )
        return [self._change_from_store_row(row) for row in rows]

    def filter(self, filter_rules: Optional[list[str]] = None) -> int:
        return self._get_store().toggle_apply(filter_rules=filter_rules)

    def has_changes(self) -> bool:
        return len(self.changes) > 0

    def unify_rule_changes(self, cube_rule_texts: Optional[dict[str, str]] = None) -> None:
        """Collapse per-cube Rule add/remove/modify entries into one modify Rule entry.

        The unified Rule entry uses:
        - name: "default"
        - change_type: "modify"
        - uri: Cubes('<cube>')/Rules('default')
        - full_statement: full unified rule text for the cube
        """
        cube_rule_texts = cube_rule_texts or {}
        before_count = len(self.changes)
        grouped_rule_changes: dict[str, list[Change]] = {}
        non_rule_changes: list[Change] = []

        for change in self.changes:
            if change.object_type != ObjectType.RULE:
                non_rule_changes.append(change)
                continue

            cube_name = Rule.cube_name_from_uri(change.uri)
            if not cube_name:
                non_rule_changes.append(change)
                continue
            grouped_rule_changes.setdefault(cube_name, []).append(change)

        unified_rule_changes: list[Change] = []
        for cube_name in sorted(grouped_rule_changes.keys()):
            unified_text = cube_rule_texts.get(cube_name)
            if unified_text is None:
                unified_text = _compose_rule_text_from_changes(grouped_rule_changes[cube_name])

            rule_uri = Rule.uri_for(cube_name)
            unified_rule_changes.append(
                Change(
                    change_type=ChangeType.MODIFY,
                    object_type=ObjectType.RULE,
                    uri=rule_uri,
                    body=Rule(
                        name="default",
                        area="[default]",
                        full_statement=unified_text,
                        comment="",
                    ),
                )
            )

        self.changes = non_rule_changes + unified_rule_changes
        logger.debug(
            "Unified rule changes (before=%d after=%d cubes=%d)",
            before_count,
            len(self.changes),
            len(unified_rule_changes),
        )

    def apply(
            self,
            tm1_service,
            *,
            status_dir: Optional[Union[str, Path]] = None,
            execution_id: Optional[str] = None,
            fail_fast: bool = True
    ) -> tuple[bool, Union[list, None]]:
        from tm1_git_py.apply import apply as apply_changeset

        return apply_changeset(
            changeset=self,
            tm1_service=tm1_service,
            status_dir=status_dir,
            execution_id=execution_id,
            fail_fast=fail_fast
        )


    def sort(self):
        logger.debug("Changeset.sort() is a no-op; ordering is SQL-backed.")


    def export(self, file_path: Union[str, Path]) -> None:
        """Export changeset payload to JSON (streaming) or YAML."""

        logger.info("Exporting changeset '%s' to '%s'", self.changeset_id, file_path)
        output_path = Path(file_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = output_path.suffix.lower()
        if suffix == ".json":
            store = self._get_store()
            summary = store.summary_counts()
            with output_path.open("w", encoding="utf-8") as handle:
                handle.write("{\n")
                handle.write(f'  "changeset_id": {json.dumps(self.changeset_id, ensure_ascii=False)},\n')
                handle.write(f'  "summary": {json.dumps(summary, ensure_ascii=False)},\n')
                handle.write('  "changes": [\n')
                first = True
                for row in store.iter_rows(sorted_order=True):
                    change = self._change_from_store_row(row)
                    entry = {
                        "change_type": change.change_type.value,
                        "object_type": change.object_type.value,
                        "uri": change.uri,
                        "apply": change.apply,
                        "body": _serialize_change_body(change),
                    }
                    prefix = "    " if first else "    ,"
                    handle.write(prefix + json.dumps(entry, ensure_ascii=False) + "\n")
                    first = False
                handle.write("  ]\n")
                handle.write("}\n")
        else:
            payload = self.to_json()
            output_path.write_text(
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        logger.info("Exported changeset '%s' with %d change(s)", self.changeset_id, len(self.changes))

    def to_json(self) -> dict[str, Any]:
        """Build a fixture-compatible changeset payload as a JSON-serializable dict."""

        self.sort()
        logger.info(
            "Serializing changeset '%s' to payload (changes=%d)",
            self.changeset_id,
            len(self.changes),
        )

        export_entries = []
        summary = {"add": 0, "remove": 0, "modify": 0}
        for change in self.changes:
            change_type = change.change_type
            if hasattr(change_type, "value"):
                change_type = change.change_type.value
            export_entries.append({
                "change_type": change_type,
                "object_type": change.object_type.value,
                "uri": change.uri,
                "apply": change.apply,
                "body": _serialize_change_body(change),
            })
            summary[change_type] = summary.get(change_type, 0) + 1

        payload = {
            "changeset_id": self.changeset_id,
            "summary": summary,
            "changes": export_entries
        }
        logger.debug(
            "Serialized changeset summary add=%d remove=%d modify=%d",
            summary.get("add", 0),
            summary.get("remove", 0),
            summary.get("modify", 0),
        )
        return payload


# --------------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------------

def normalize_reference_path(reference_path: str) -> str:
    if not reference_path:
        return ""

    normalized = reference_path.replace("\\", "/").lstrip("/")
    if normalized.endswith(".json"):
        normalized = normalized[:-5]

    return normalized


def _path_stem(reference_path: Optional[str]) -> str:
    if not reference_path:
        return ""
    normalized = reference_path.replace("\\", "/")
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


def _compose_rule_text_from_changes(changes: list[Change]) -> str:
    """Fallback when full target cube rule text is not provided."""
    parts: list[str] = []
    for change in changes:
        if change.change_type == ChangeType.REMOVE:
            continue
        body = change.body
        if isinstance(body, Rule):
            if body.comment:
                parts.append(body.comment)
            parts.append(body.full_statement)
    return "\n\n".join(parts)


def _remove_body_name(body: Any) -> str:
    if isinstance(body, Rule):
        return getattr(body, "name", None) or _rule_name_from_area(body.area)
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
            "name": body.name,
            "rule": body.full_statement,
            "area": body.area,
            "comment": body.comment,
        }

    if isinstance(body, Cube):
        return {
            "name": body.name,
            "dimensions": [_resolve_change_body_reference_path(d) or f"dimensions/{d.name}.json" for d in body.dimensions]
        }

    if isinstance(body, Subset):
        return {
            "name": body.name,
            "expression": body.expression
        }

    if isinstance(body, Edge):
        return {
            "ParentName": body.parent,
            "ComponentName": body.name,
            "Weight": body.weight,
        }

    if isinstance(body, Element):
        return {
            "Name": body.name,
            "Type": body.type,
        }

    if isinstance(body, Hierarchy):
        return {
            "name": body.name
        }

    if isinstance(body, Dimension):
        return {
            "name": body.name,
            "hierarchies": [
                _resolve_change_body_reference_path(h, dimension_name=body.name)
                or f"dimensions/{body.name}.hierarchies/{h.name}.json"
                for h in body.hierarchies
            ],
            "default_hierarchy": (
                _resolve_change_body_reference_path(body.defaultHierarchy, dimension_name=body.name)
                or f"dimensions/{body.name}.hierarchies/{body.defaultHierarchy.name}.json"
            )
        }

    if isinstance(body, Process):
        ti = getattr(body, "ti", None)
        return {
            "name": body.name,
            "has_security_access": body.hasSecurityAccess,
            "data_source": body.datasource if isinstance(body.datasource, dict) else {"type": body.datasource or "None"},
            "parameters": body.parameters,
            "variables": body.variables,
            "prolog": getattr(ti, "prolog_procedure", ""),
            "data": getattr(ti, "data_procedure", ""),
            "metadata": getattr(ti, "metadata_procedure", ""),
            "epilog": getattr(ti, "epilog_procedure", ""),
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
        reference_path: str
) -> dict[str, Any]:
    normalized = copy.deepcopy(payload or {})

    if object_type == ObjectType.EDGE:
        if "ParentName" not in normalized:
            if "parent_name" in normalized:
                normalized["ParentName"] = normalized["parent_name"]
            elif "parentName" in normalized:
                normalized["ParentName"] = normalized["parentName"]
        if "ComponentName" not in normalized:
            if "component_name" in normalized:
                normalized["ComponentName"] = normalized["component_name"]
            elif "componentName" in normalized:
                normalized["ComponentName"] = normalized["componentName"]
        if "Weight" not in normalized and "weight" in normalized:
            normalized["Weight"] = normalized["weight"]

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
            process_name = normalized.get("name") or _path_stem(reference_path) or "process"
            normalized["code_link"] = f"{process_name}.ti"

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

def _extract_json_string_field(line: str, field_name: str) -> Optional[str]:
    match = re.search(rf'"{re.escape(field_name)}"\s*:\s*"([^"]*)"', line)
    if not match:
        return None
    return str(match.group(1))


def _read_changeset_json_metadata(changeset_file: Union[str, Path]) -> Optional[str]:
    path = Path(changeset_file).expanduser().resolve()
    payload_id: Optional[str] = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if payload_id is None:
                payload_id = _extract_json_string_field(line, "changeset_id") or payload_id
            if payload_id is not None:
                break
    return payload_id


def _line_stream_json_changes(changeset_file: Union[str, Path]) -> Iterator[dict[str, Any]]:
    path = Path(changeset_file).expanduser().resolve()
    def _iter_entries() -> Iterator[dict[str, Any]]:
        in_changes = False
        depth = 0
        in_string = False
        escape = False
        buffer: list[str] = []

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not in_changes:
                    if '"changes"' in line and "[" in line:
                        in_changes = True
                    continue

                stripped = line.strip()
                if depth == 0 and stripped.startswith("]"):
                    break

                start_idx = 0
                if depth == 0:
                    brace_idx = line.find("{")
                    if brace_idx < 0:
                        continue
                    start_idx = brace_idx

                chunk = line[start_idx:]
                buffer.append(chunk)
                for ch in chunk:
                    if escape:
                        escape = False
                        continue
                    if ch == "\\":
                        escape = True
                        continue
                    if ch == '"':
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1

                if depth == 0 and buffer:
                    entry_raw = "".join(buffer).strip().rstrip(",")
                    buffer = []
                    if not entry_raw:
                        continue
                    try:
                        parsed = json.loads(entry_raw)
                        if isinstance(parsed, dict):
                            yield parsed
                    except Exception:
                        continue

    return _iter_entries()


def _read_changeset_yaml_metadata(changeset_file: Union[str, Path]) -> Optional[str]:
    path = Path(changeset_file).expanduser().resolve()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("changeset_id:"):
                _key, _sep, value = stripped.partition(":")
                value = value.strip().strip("'\"")
                if value and value.lower() not in {"null", "none"}:
                    return value
                return None
    return None


def _line_stream_yaml_changes(changeset_file: Union[str, Path]) -> Iterator[dict[str, Any]]:
    path = Path(changeset_file).expanduser().resolve()
    in_changes = False
    current_block: list[str] = []

    def _emit_block() -> Optional[dict[str, Any]]:
        if not current_block:
            return None
        raw = "\n".join(current_block)
        try:
            parsed = yaml.safe_load(raw)
        except Exception:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not in_changes:
                if line.strip() == "changes:":
                    in_changes = True
                continue

            if line.startswith("- "):
                parsed = _emit_block()
                if parsed is not None:
                    yield parsed
                current_block = [line[2:].rstrip("\n")]
                continue

            if current_block and (line.startswith("  ") or line.strip() == ""):
                current_block.append(line[2:].rstrip("\n") if line.startswith("  ") else "")
                continue

            if current_block:
                parsed = _emit_block()
                if parsed is not None:
                    yield parsed
                current_block = []

    parsed = _emit_block()
    if parsed is not None:
        yield parsed


def import_changeset(changeset_file: Union[str, Path]) -> Changeset:
    """
    Build a Changeset from fixture-style YAML/JSON and stream JSON entries into sqlite.
    """
    logger.info("Importing changeset from '%s'", changeset_file)
    path = Path(changeset_file).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Changeset file not found: {path}")

    if path.suffix.lower() == ".json":
        payload_id = _read_changeset_json_metadata(path)
        entries = _line_stream_json_changes(path)
        changeset = Changeset(changeset_id=payload_id or None)
        changeset._get_store().clear()
        for entry in entries:
            _append_import_entry(changeset, entry)
        logger.info(
            "Imported JSON changeset '%s' with %d change(s), import errors=%d",
            changeset.changeset_id,
            len(changeset.changes),
            len(changeset.errors.get("import", [])),
        )
        return changeset

    payload_id = _read_changeset_yaml_metadata(path)
    changeset = Changeset(changeset_id=payload_id or None)
    changeset._get_store().clear()
    for entry in _line_stream_yaml_changes(path):
        _append_import_entry(changeset, entry)

    logger.info(
        "Imported changeset '%s' with %d change(s), import errors=%d",
        changeset.changeset_id,
        len(changeset.changes),
        len(changeset.errors.get("import", [])),
    )
    return changeset


def _append_import_entry(changeset: Changeset, entry: Any) -> None:
    if not isinstance(entry, dict):
        logger.warning("Skipping malformed changeset entry: %s", entry)
        return
    try:
        change_type = ChangeType.from_raw(entry.get("change_type"))
        object_type = ObjectType.from_raw(entry.get("object_type"))
        raw_uri = (entry.get("uri") or "").strip().replace("\\", "/")
        raw_legacy = (entry.get("source_path") or "").strip().replace("\\", "/")
        body_payload = entry.get("body") or {}
        if raw_uri:
            uri_val = raw_uri
        elif raw_legacy:
            uri_val = raw_legacy
        else:
            raise ValueError("Missing uri (or legacy source_path)")
        apply_flag = entry.get("apply")

        normalized_payload = _normalize_body_payload(object_type, body_payload, uri_val)
        body_object = _deserialize_object_from_payload(object_type.value, normalized_payload, uri_val)
        if body_object is None:
            raise ValueError("Failed to deserialize body")

        changeset.changes.append(
            Change(
                change_type=change_type,
                object_type=object_type,
                uri=uri_val,
                body=body_object,
                apply=True if apply_flag is None else _as_bool(apply_flag),
            )
        )
    except Exception as exc:
        logger.warning(
            "Skipping unsupported/malformed changeset entry object_type=%s uri=%s error=%s",
            entry.get("object_type"),
            entry.get("uri") or entry.get("source_path"),
            exc,
        )
        changeset.errors.setdefault("import", []).append({
            "entry": entry,
            "error": str(exc),
        })


def _build_dimension_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> Dimension:
    return Dimension.from_dict(payload)


def _build_hierarchy_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> Hierarchy:
    dimension_name, _ = hierarchy._hierarchy_context_from_uri(reference_path)
    if not dimension_name:
        raise ValueError("Hierarchy payload missing dimension context.")
    return Hierarchy.from_dict(payload)


def _build_subset_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> Subset:
    dimension_name, hierarchy_name = subset._subset_context_from_uri(reference_path)
    if not dimension_name or not hierarchy_name:
        raise ValueError("Subset payload missing dimension or hierarchy context.")
    return Subset.from_dict(payload)


def _build_cube_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> Cube:
    return Cube.from_dict(payload)


def _build_mdx_view_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> MDXView:
    if not reference_path:
        raise ValueError("MDXView payload missing cube context.")
    return MDXView.from_dict(payload)


def _build_native_view_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> NativeView:
    if not reference_path:
        raise ValueError("NativeView payload missing cube context.")
    return NativeView.from_dict(payload)


def _build_process_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> Process:
    return Process.from_dict(payload)


def _build_chore_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> Chore:
    return Chore.from_dict(payload)

def _build_element_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> Element:
    return Element.from_dict(payload)


def _build_edge_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> Edge:
    return Edge.from_dict(payload)


def _build_rule_from_payload(payload: dict[str, Any], reference_path: Optional[str]) -> Rule:
    normalized_payload = dict(payload)
    if "rule" in normalized_payload and "statement" not in normalized_payload and "full_statement" not in normalized_payload:
        normalized_payload["statement"] = normalized_payload["rule"]
    if "name" not in normalized_payload:
        normalized_payload["name"] = _rule_name_from_area(normalized_payload.get("area", ""))
    return Rule.from_dict(normalized_payload)


_OBJECT_BUILDERS: dict[str, Any] = {
    "Dimension": _build_dimension_from_payload,
    "Hierarchy": _build_hierarchy_from_payload,
    "Subset": _build_subset_from_payload,
    "Cube": _build_cube_from_payload,
    "MDXView": _build_mdx_view_from_payload,
    "NativeView": _build_native_view_from_payload,
    "Process": _build_process_from_payload,
    "Chore": _build_chore_from_payload,
    "Element": _build_element_from_payload,
    "Edge": _build_edge_from_payload,
    "Rule": _build_rule_from_payload,
    "TIProcess": _build_process_from_payload,
}


def _deserialize_object_from_payload(object_type: Optional[str],
                                     payload: Optional[dict[str, Any]],
                                     reference_path: Optional[str]) -> Optional[Any]:
    if not payload or not object_type:
        return None
    builder = _OBJECT_BUILDERS.get(object_type)
    if builder is None:
        raise ValueError(f"Unsupported object type '{object_type}' in changeset import.")
    return builder(payload, reference_path)


def _resolve_change_body_reference_path(body: Any, **context: Any) -> str:
    try:
        if isinstance(body, Rule):
            cube_name = context.get("cube_name")
            return f"cubes/{cube_name}.rules" if cube_name else ""
        if isinstance(body, (MDXView, NativeView)):
            cube_name = context.get("cube_name")
            return f"cubes/{cube_name}.views/{body.name}.json" if cube_name else ""
        if isinstance(body, Hierarchy):
            dimension_name = context.get("dimension_name")
            return f"dimensions/{dimension_name}.hierarchies/{body.name}.json" if dimension_name else ""
        if isinstance(body, Subset):
            dimension_name = context.get("dimension_name")
            hierarchy_name = context.get("hierarchy_name")
            return f"dimensions/{dimension_name}.hierarchies/{hierarchy_name}.subsets/{body.name}.json" if dimension_name and hierarchy_name else ""
        if isinstance(body, Element):
            dimension_name = context.get("dimension_name")
            hierarchy_name = context.get("hierarchy_name")
            return f"dimensions/{dimension_name}.hierarchies/{hierarchy_name}.json/{body.name}" if dimension_name and hierarchy_name else ""
        if isinstance(body, Edge):
            dimension_name = context.get("dimension_name")
            hierarchy_name = context.get("hierarchy_name")
            return f"dimensions/{dimension_name}.hierarchies/{hierarchy_name}.json/{body.parent}:{body.name}" if dimension_name and hierarchy_name else ""
        if isinstance(body, Dimension):
            return f"dimensions/{body.name}.json"
        if isinstance(body, Cube):
            return f"cubes/{body.name}"
        if isinstance(body, Process):
            return f"processes/{body.name}.json"
        if isinstance(body, Chore):
            return f"chores/{body.name}.json"
    except Exception:
        return ""
    return ""


