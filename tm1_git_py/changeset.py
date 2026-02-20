import copy
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional, TypeVar, Union

from TM1py.Services import TM1Service
from requests import Response

from tm1_git_py.changeset_status import ChangeSetStatusStore
from tm1_git_py.model import Chore, Cube, Dimension, Hierarchy, MDXView, Model, Process, Subset, hierarchy, subset, \
    mdxview
from tm1_git_py.model.chore import create_chore, delete_chore, update_chore
from tm1_git_py.model.cube import create_cube, delete_cube, update_cube
from tm1_git_py.model.dimension import create_dimension, delete_dimension, update_dimension
from tm1_git_py.model.hierarchy import create_hierarchy, delete_hierarchy, update_hierarchy
from tm1_git_py.model.mdxview import create_mdx_view, delete_mdx_view, update_mdx_view
from tm1_git_py.model.process import create_process, delete_process, update_process
from tm1_git_py.model.subset import create_subset, delete_subset, update_subset

logger = logging.getLogger(__name__)


T = TypeVar('T', Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore)

_CHILD_RELATIONS: dict[type, list[str]] = {
    Dimension: ["hierarchies"],
    Hierarchy: ["subsets"],
    Cube: ["views"],
}

FLAG_PRECEDENCE = {"C": 0, "U": 1, "D": 2}
OBJECT_PRECEDENCE = {'dimensions': 0, 'hierarchies': 1, 'subsets': 2, 'cubes': 3, 'views': 4, 'processes': 5, 'chores': 6}
DELETE_OBJECT_PRECEDENCE = {'views': 0, 'cubes': 1, 'subsets': 2, 'hierarchies': 3, 'dimensions': 4, 'chores': 5, 'processes': 6}

FILTER_SEMAPHORE_OBJECT_TYPES = ("Dimension", "Hierarchy", "Subset", "Element", "Edge", "MDXView")


def normalize_source_path(source_path: str) -> str:
    if not source_path:
        return ""

    normalized = source_path.replace("\\", "/").lstrip("/")
    if normalized.endswith(".json"):
        normalized = normalized[:-5]

    return normalized


def _sort_change_line_key(s: str):
    """
    Sorting key for a single textual change line like:
        'C  /dimensions/Account'
        'U  /cubes/Sales'
        'D  /dimensions/Account.hierarchies/Total'
    """
    changes_precedence = OBJECT_PRECEDENCE
    flag = re.search(r'\A([UDC])', s).group(1)
    obj_name = re.search(r'/\b(\w*)/', s).group(1)

    if 'subsets' in s:
        obj_name = 'subsets'
    elif 'hierarchies' in s:
        obj_name = 'hierarchies'
    elif 'views' in s:
        obj_name = "views"

    source_path = s.split(obj_name)[1]

    if flag == 'D':
        changes_precedence = DELETE_OBJECT_PRECEDENCE

    key = (
        FLAG_PRECEDENCE.get(flag, 99),
        changes_precedence.get(obj_name, 99),
        source_path
    )

    return key


def _source_path_sort_key(s: Union[T, dict[T, Any]], delete_precedence = False):
    """
    Sorting key for model objects based on their .source_path, used to sort:
      - added
      - removed
      - modified (by new object)
    """
    if isinstance(s, (Cube, MDXView, Dimension, Hierarchy, Subset, Chore, Process)):
        s = s.source_path
    else:
        raise ValueError(f"Cannot sort object type for source path '{s}'")

    s = s.lstrip("/")
    obj_match = re.search(r'(\w*)/', s)
    if not obj_match:
        raise ValueError(f"Cannot extract object name from source path '{s}'")
    obj_name = obj_match.group(1)

    if 'subsets' in s:
        obj_name = 'subsets'
    elif 'hierarchies' in s:
        obj_name = 'hierarchies'

    source_path = s.split(obj_name)[1]

    if not delete_precedence:
        key = (
            OBJECT_PRECEDENCE.get(obj_name, 99),
            source_path
        )
    else:
        key = (
            DELETE_OBJECT_PRECEDENCE.get(obj_name, 99),
            source_path
        )

    return key


def _iter_changeset_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Normalize changeset payload entries to a flat list with action.
    Supports both legacy format:
      "changes": [{"action": "...", ...}]
    and grouped format:
      "changes": {"added": [...], "removed": [...], "modified": [...]}
    """
    changes = payload.get("changes", [])

    if isinstance(changes, list):
        return [entry for entry in changes if isinstance(entry, dict)]

    if isinstance(changes, dict):
        entries: list[dict[str, Any]] = []
        for action, key in (("CREATE", "added"), ("DELETE", "removed"), ("UPDATE", "modified")):
            for entry in changes.get(key, []) or []:
                if not isinstance(entry, dict):
                    continue
                normalized = dict(entry)
                normalized["action"] = action
                entries.append(normalized)
        return entries

    raise ValueError("changeset payload must contain either a list or an object under 'changes'")


class Changeset:

    model: Model

    def __init__(self, baseline_model: Optional[Model] = None):

        self.added: list[T] = []
        self.modified: list[dict[T, Any]] = []
        self.removed: list[T] = []

        self.changes: list[str] = []
        self.last_execution_id: str = '0'
        self.model: Optional[Model] = baseline_model

        self.errors: dict[str, list[Any]] = {}

    @property
    def all_removed(self) -> list[str]:
        return self.removed

    @property
    def lines(self) -> list[str]:
        return self._build_changes()

    def __repr__(self):
        changes = self.lines
        if not changes:
            return "No changes"
        return "Changeset:\n" + "\n".join(changes)

    def add_created(self, obj: Any, *, message: Optional[str] = None) -> None:
        self.added.append(obj)

    def add_deleted(self, obj: Any, *, message: Optional[str] = None) -> None:
        self.removed.append(obj)

    def add_modified(
            self,
            old: Any,
            new: Any,
            *,
            changes: Optional[str] = None,
    ) -> None:
        self.modified.append(
            {
                "old": old,
                "new": new,
                "changes": changes or "Content changed.",
            }
        )

    def has_changes(self) -> bool:
        return any([self.added, self.modified, self.removed])


    def _build_changes(self) -> list[str]:
        """
        Build the normalized, sorted 'C/U/D  path' lines from the current
        added/modified/removed lists. No side effects.
        """
        lines: list[str] = []

        for obj in self.added:
            path = normalize_source_path(getattr(obj, "source_path", ""))
            if path:
                lines.append(f"C  /{path}")

        for mod in self.modified:
            new_obj = mod["new"]
            path = normalize_source_path(getattr(new_obj, "source_path", ""))
            if path:
                lines.append(f"U  /{path}")

        for obj in self.removed:
            path = normalize_source_path(getattr(obj, "source_path", ""))
            if path:
                lines.append(f"D  /{path}")

        lines.sort(key=_sort_change_line_key)
        return lines


    def apply(
        self,
        tm1_service: TM1Service,
        *,
        status_dir: Optional[Union[str, Path]] = None,
        execution_id: Optional[str] = None,
        changeset_name: Optional[str] = None,
        fail_fast: bool = True,
        **kwargs
    ) -> tuple[bool, Union[list, None]]:

        changes = []
        if not self.has_changes():
            logger.info("No changes to apply.")
            return True, None

        self.sort()

        operations: list[tuple[str, Any]] = []
        operations += [("CREATE", obj) for obj in self.added]
        operations += [("UPDATE", obj) for obj in self.modified]
        operations += [("DELETE", obj) for obj in self.removed]

        store: Optional[ChangeSetStatusStore] = None
        if status_dir is not None:
            store = ChangeSetStatusStore(status_dir=status_dir, execution_id=execution_id,
                                         changeset_name=changeset_name)
            store.start(total_operations=len(operations))
            self.last_execution_id = store.execution_id
            logger.info("changeset execution_id=%s status_file=%s", store.execution_id, store.path)

        def _obj_meta(o: Any) -> tuple[str, Optional[str], Optional[str]]:
            if isinstance(o, dict) and "new" in o:
                o = o["new"]
            return o.__class__.__name__, getattr(o, "name", None), getattr(o, "source_path", None)

        ok_all = True

        for i, (action, obj) in enumerate(operations, start=1):
            obj_type, obj_name, obj_path = _obj_meta(obj)

            if store is not None:
                store.begin_operation(i, action, obj_type, obj_name, obj_path)

            try:
                if action == "CREATE":
                    resp = create_object(tm1_service=tm1_service, object_instance=obj)
                elif action == "UPDATE":
                    resp = update_object(tm1_service=tm1_service, object_instance=obj, **kwargs)
                elif action == "DELETE":
                    resp = delete_object(tm1_service=tm1_service, object_instance=obj)
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

    def sort(self):
        if self.has_changes():
            self.changes.sort(key=_sort_change_line_key)

            self.added.sort(key=_source_path_sort_key)

            self.modified.sort(
                key=lambda m: _source_path_sort_key(m["new"])
            )

            self.removed.sort(
                key=lambda obj: _source_path_sort_key(obj, delete_precedence=True)
            )


    def export(self, file_path: Union[str, Path]) -> None:
        """
        Export a detailed representation of the changeset so it can be recreated later.
        """

        def _serialize_obj(obj: Optional[Any]) -> Optional[dict[str, Any]]:
            if obj is None:
                return None
            if hasattr(obj, "to_dict"):
                try:
                    return copy.deepcopy(obj.to_dict())
                except Exception as exc:
                    logger.error("Failed serializing %s to dict for export: %s", type(obj).__name__, exc)
                    raise
            raise ValueError(f"Object '{obj}' does not support to_dict()")

        def _serialize_entry(old_obj: Optional[Any],
                             new_obj: Optional[Any],
                             message: Optional[str] = None,
                             apply: Optional[bool] = None) -> dict[str, Any]:
            obj_for_meta = new_obj or old_obj
            object_type = obj_for_meta.__class__.__name__ if obj_for_meta is not None else None
            object_name = getattr(obj_for_meta, "name", None) if obj_for_meta is not None else None
            source_path = getattr(obj_for_meta, "source_path", None) if obj_for_meta is not None else None
            apply_value = apply if apply is not None else bool(getattr(obj_for_meta, "apply", True))
            serialized = {
                "apply": apply_value,
                "object_type": object_type,
                "object_name": object_name,
                "source_path": source_path if source_path else None,
                "difference": {
                    "old_object": _serialize_obj(old_obj),
                    "new_object": _serialize_obj(new_obj)
                }
            }
            if message:
                serialized["difference"]["message"] = message
            return serialized

        def _build_filter_semaphore(changes_by_action: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
            state = {
                key: {"has_added_or_modified": False, "has_removed": False, "has_false_apply": False, "has_any": False}
                for key in FILTER_SEMAPHORE_OBJECT_TYPES
            }

            for action, entries in changes_by_action.items():
                for entry in entries:
                    obj_type = entry.get("object_type")
                    if obj_type not in state:
                        continue

                    entry_state = state[obj_type]
                    entry_state["has_any"] = True
                    if action == "removed":
                        entry_state["has_removed"] = True
                    else:
                        entry_state["has_added_or_modified"] = True
                    if not entry.get("apply", True):
                        entry_state["has_false_apply"] = True

            filter_semaphore: dict[str, dict[str, Any]] = {}
            for key in FILTER_SEMAPHORE_OBJECT_TYPES:
                entry_state = state[key]
                if not entry_state["has_any"]:
                    color = None
                    sign = None
                else:
                    has_removed = entry_state["has_removed"]
                    has_add_mod = entry_state["has_added_or_modified"]
                    if has_removed and not has_add_mod:
                        color = "red"
                    elif has_removed and has_add_mod:
                        color = "yellow"
                    else:
                        color = "green"
                    sign = not entry_state["has_false_apply"]

                filter_semaphore[key] = {"color": color, "sign": sign}

            return filter_semaphore

        self.sort()

        export_entries = {
            "added": [_serialize_entry(None, obj) for obj in self.added],
            "removed": [_serialize_entry(obj, None) for obj in self.removed],
            "modified": [
                _serialize_entry(mod.get("old"), mod.get("new"), mod.get("changes"), mod.get("apply"))
                for mod in self.modified
            ],
        }

        status = "ok"
        if self.errors:
            status = "error"

        output_path = Path(file_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "changeset_name": None,
            "status": status,
            "summary": {
                "added": len(self.added),
                "removed": len(self.removed),
                "modified": len(self.modified),
            },
            "changes": export_entries,
            "filter_semaphore": _build_filter_semaphore(export_entries),
            "errors": self.errors,
        }
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------------
# Import changeset function & helpers
# --------------------------------------------------------------------------------

def import_changeset(changeset_file: Union[str, Path], *, base_model: Optional[Model] = None) -> Changeset:
    if base_model:
        return import_changeset_stateful(base_model=base_model, changeset_file=changeset_file)
    return import_changeset_stateless(changeset_file=changeset_file)


def import_changeset_stateful(base_model: Model, changeset_file: Union[str, Path]) -> Changeset:
    """
    command line tool apply: export a changeset file -> import file and old model to recreate changeset -> apply it
    """
    try:
        payload = _load_changeset_payload(changeset_file)
    except Exception as exc:
        logger.error("Failed to load changeset payload from '%s': %s", changeset_file, exc)
        raise

    entries = _iter_changeset_entries(payload)

    base_index = _build_path_index(base_model)
    changeset = Changeset(baseline_model=base_model)

    for entry in entries:
        if not isinstance(entry, dict):
            logger.warning("Skipping malformed changeset entry: %s", entry)
            continue

        action = entry.get("action")
        object_type = entry.get("object_type")
        source_path = entry.get("source_path")
        diff = entry.get("difference") or {}
        new_payload = diff.get("new_object")
        message = diff.get("message")

        old_obj = _build_or_resolve_object(object_type, None, source_path, base_index, prefer_payload=False)

        if action == "CREATE":
            if new_payload is None:
                raise ValueError(f"Missing new_object payload for CREATE entry {entry}")
            if old_obj is not None:
                logger.warning("CREATE entry for existing %s at '%s' treated as UPDATE.",
                               object_type, source_path)
                new_obj = _apply_payload_to_old(object_type, old_obj, new_payload, source_path)
                message = message or f"Content of {object_type} '{getattr(old_obj, 'name', '')}' changed."
                changeset.add_modified(old=old_obj, new=new_obj, changes=message)
            else:
                new_obj = _deserialize_object_from_payload(object_type, new_payload, source_path)
                changeset.add_created(new_obj)
        elif action == "DELETE":
            if old_obj is None:
                logger.warning("DELETE entry for missing %s at '%s' skipped.", object_type, source_path)
                continue
            changeset.add_deleted(old_obj)
        elif action == "UPDATE":
            if old_obj is None:
                logger.warning("UPDATE entry for missing %s at '%s' skipped.", object_type, source_path)
                continue
            if new_payload is None:
                raise ValueError(f"Missing new_object payload for UPDATE entry {entry}")
            new_obj = _apply_payload_to_old(object_type, old_obj, new_payload, source_path)
            if not message:
                target_name = entry.get("object_name") or getattr(new_obj, "name", "")
                message = f"Content of {object_type} '{target_name}' changed."
            changeset.add_modified(old=old_obj, new=new_obj, changes=message)
        else:
            logger.warning("Unknown action '%s' in entry %s", action, entry)

    changeset.sort()
    return changeset


def import_changeset_stateless(changeset_file: Union[str, Path]) -> Changeset:
    """
    Rebuild a changeset from an exported changeset file without requiring a base model.
    """
    try:
        payload = _load_changeset_payload(changeset_file)
    except Exception as exc:
        logger.error("Failed to load changeset payload from '%s': %s", changeset_file, exc)
        raise

    entries = _iter_changeset_entries(payload)

    changeset = Changeset(baseline_model=None)

    for entry in entries:
        if not isinstance(entry, dict):
            logger.warning("Skipping malformed changeset entry: %s", entry)
            continue

        action = entry.get("action")
        object_type = entry.get("object_type")
        source_path = entry.get("source_path")
        diff = entry.get("difference") or {}
        new_payload = diff.get("new_object")
        old_payload = diff.get("old_object")
        message = diff.get("message")

        if action == "CREATE":
            if new_payload is None:
                raise ValueError(f"Missing new_object payload for CREATE entry {entry}")
            new_obj = _deserialize_object_from_payload(object_type, new_payload, source_path)
            if new_obj is None:
                raise ValueError(f"Failed to deserialize CREATE entry {entry}")
            changeset.add_created(new_obj)
        elif action == "DELETE":
            if old_payload is None:
                raise ValueError(f"Missing old_object payload for DELETE entry {entry}")
            old_obj = _deserialize_object_from_payload(object_type, old_payload, source_path)
            if old_obj is None:
                raise ValueError(f"Failed to deserialize DELETE entry {entry}")
            changeset.add_deleted(old_obj)
        elif action == "UPDATE":
            if new_payload is None or old_payload is None:
                raise ValueError(f"Missing old_object or new_object payload for UPDATE entry {entry}")
            old_obj = _deserialize_object_from_payload(object_type, old_payload, source_path)
            new_obj = _deserialize_object_from_payload(object_type, new_payload, source_path)
            if old_obj is None or new_obj is None:
                raise ValueError(f"Failed to deserialize UPDATE entry {entry}")
            if not message:
                target_name = entry.get("object_name") or getattr(new_obj, "name", "")
                message = f"Content of {object_type} '{target_name}' changed."
            changeset.add_modified(old=old_obj, new=new_obj, changes=message)
        else:
            logger.warning("Unknown action '%s' in entry %s", action, entry)

    changeset.sort()
    return changeset


def _load_changeset_payload(changeset_file) -> dict[str, Any]:
    if hasattr(changeset_file, "read"):
        return json.load(changeset_file)

    with open(changeset_file, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _build_path_index(model: Model) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for collection in (model.cubes, model.dimensions, model.processes, model.chores):
        for obj in collection:
            _index_object_paths(index, obj)

    return index


def _resolve_path(path: str, index: dict[str, Any]) -> Any:
    if not path:
        return None

    if path in index:
        return index[path]

    normalized = normalize_source_path(path)
    if normalized in index:
        return index[normalized]

    candidate_with_json = f"{normalized}.json"
    if candidate_with_json in index:
        return index[candidate_with_json]

    return None


def _index_object_paths(index: dict[str, Any], obj: Any) -> None:
    source_path = getattr(obj, "source_path", None)
    if source_path:
        normalized = normalize_source_path(source_path)
        if normalized:
            index[normalized] = obj
            index[f"{normalized}.json"] = obj

    for child_attr in _CHILD_RELATIONS.get(type(obj), []):
        children = getattr(obj, child_attr, None) or []
        for child in children:
            _index_object_paths(index, child)


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


_OBJECT_BUILDERS: dict[str, Any] = {
    "Dimension": _build_dimension_from_payload,
    "Hierarchy": _build_hierarchy_from_payload,
    "Subset": _build_subset_from_payload,
    "Cube": _build_cube_from_payload,
    "MDXView": _build_mdx_view_from_payload,
    "Process": _build_process_from_payload,
    "Chore": _build_chore_from_payload
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


def _build_or_resolve_object(object_type: Optional[str],
                             payload: Optional[dict[str, Any]],
                             source_path: Optional[str],
                             index: dict[str, Any],
                             prefer_payload: bool = True) -> Optional[Any]:
    obj = None
    if prefer_payload:
        obj = _deserialize_object_from_payload(object_type, payload, source_path)
    if obj is None and source_path:
        rel_path = normalize_source_path(source_path)
        if rel_path:
            obj = _resolve_path(rel_path, index)
    if obj is None and not prefer_payload:
        obj = _deserialize_object_from_payload(object_type, payload, source_path)
    return obj


def _deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _apply_payload_to_old(object_type: Optional[str],
                          old_obj: Any,
                          new_payload: dict[str, Any],
                          source_path: Optional[str]) -> Any:
    if object_type is None:
        raise ValueError("Object type is required to apply payload.")
    base_dict = old_obj.to_dict()
    merged = _deep_merge_dict(base_dict, new_payload)
    merged["name"] = merged.get("name") or getattr(old_obj, "name", None)
    return _deserialize_object_from_payload(object_type, merged, source_path)


# --------------------------------------------------------------------------------
# CRUD operations for apply changeset function
# --------------------------------------------------------------------------------

def create_object(tm1_service: TM1Service, object_instance: T) -> Response:
    if isinstance(object_instance, Dimension):
        return create_dimension(tm1_service=tm1_service, dimension=object_instance)

    elif isinstance(object_instance, Hierarchy):
        return create_hierarchy(tm1_service=tm1_service, hierarchy=object_instance)

    elif isinstance(object_instance, Subset):
        return create_subset(tm1_service=tm1_service, subset=object_instance)

    elif isinstance(object_instance, Cube):
        return create_cube(tm1_service=tm1_service, cube=object_instance)

    elif isinstance(object_instance, MDXView):
        return create_mdx_view(tm1_service=tm1_service, mdx_view=object_instance)

    elif isinstance(object_instance, Process):
        return create_process(tm1_service=tm1_service, process=object_instance)

    elif isinstance(object_instance, Chore):
        return create_chore(tm1_service=tm1_service, chore=object_instance)

    else:
        raise ValueError


def delete_object(tm1_service: TM1Service, object_instance: T) -> Response:
    if isinstance(object_instance, MDXView):
        return delete_mdx_view(tm1_service=tm1_service, mdx_view=object_instance)

    elif isinstance(object_instance, Cube):
        return delete_cube(tm1_service=tm1_service, cube_name=object_instance.name)

    elif isinstance(object_instance, Subset):
        return delete_subset(tm1_service=tm1_service, subset=object_instance)

    elif isinstance(object_instance, Hierarchy):
        return delete_hierarchy(tm1_service=tm1_service, hierarchy=object_instance)

    elif isinstance(object_instance, Dimension):
        return delete_dimension(tm1_service=tm1_service, dimension_name=object_instance.name)

    elif isinstance(object_instance, Chore):
        return delete_chore(tm1_service=tm1_service, chore_name=object_instance.name)

    elif isinstance(object_instance, Process):
        return delete_process(tm1_service=tm1_service, process_name=object_instance.name)

    else:
        raise ValueError


def update_object(tm1_service: TM1Service, object_instance: dict[T, Any], **kwargs) -> Response:
    if isinstance(object_instance['new'], Dimension):
        return update_dimension(tm1_service=tm1_service, dimension=object_instance)

    elif isinstance(object_instance['new'], Hierarchy):
        return update_hierarchy(tm1_service=tm1_service, hierarchy=object_instance)

    elif isinstance(object_instance['new'], Subset):
        return update_subset(tm1_service=tm1_service, subset=object_instance)

    elif isinstance(object_instance['new'], Cube):
        return update_cube(tm1_service=tm1_service, cube=object_instance, **kwargs)

    elif isinstance(object_instance['new'], MDXView):
        return update_mdx_view(tm1_service=tm1_service, mdx_view=object_instance)

    elif isinstance(object_instance['new'], Process):
        return update_process(tm1_service=tm1_service, process=object_instance)

    elif isinstance(object_instance['new'], Chore):
        return update_chore(tm1_service=tm1_service, chore=object_instance)

    else:
        raise ValueError
