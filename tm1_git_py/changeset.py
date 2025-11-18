import json
import re
from pathlib import Path
from typing import List, Dict, Any, TypeVar, Optional, Union

from requests import Response

from .model import MDXView
from .model.cube import Cube, create_cube, update_cube, delete_cube
from .model.dimension import Dimension, create_dimension, update_dimension, delete_dimension
from .model.hierarchy import Hierarchy, create_hierarchy, update_hierarchy, delete_hierarchy
from .model.subset import Subset, create_subset, update_subset, delete_subset
from .model.process import Process, create_process, update_process, delete_process
from .model.chore import Chore, create_chore, update_chore, delete_chore
from TM1py import TM1Service

from tm1_git_py.model.model import Model


T = TypeVar('T', Cube, Dimension, Process, Chore)

_CHILD_RELATIONS: Dict[type, List[str]] = {
    Dimension: ["hierarchies"],
    Hierarchy: ["subsets", "edges"],
    Cube: ["views"],
}

class Changeset:

    model: Model

    def __init__(self):

        self.added: List[T] = []
        self.modified: List[Dict[T, Any]] = []
        self.removed: List[T] = []

        self.changes: List[str] = []

    @property
    def all_removed(self) -> List[str]:
        return self.removed

    def has_changes(self) -> bool:
        return any([self.added, self.modified, self.removed])

    def __repr__(self):
        changes = self._ensure_changes()
        if not changes:
            return "No changes"
        return "Changeset:\n" + "\n".join(changes)


    def _ensure_changes(self) -> List[str]:
        if not self.changes:
            lines: List[str] = []

            if self.added:
                lines.extend([f"C  /{c.source_path.removesuffix(".json")  }" for c in self.added])
            if self.removed:
                lines.extend([f"D  /{c.source_path.removesuffix(".json")}" for c in self.removed])

            if self.modified:
                lines.extend([f"U  /{c['new'].source_path.removesuffix(".json")}" for c in self.modified])

            if lines:
                self.changes = lines
                self.sort()

        return self.changes


    def apply(self, tm1_service: TM1Service) -> List[Any]:
        changes = []

        if self.has_changes():
            if self.added:
                changes += [create_object(tm1_service=tm1_service, object_instance=a).url for a in self.added]

            if self.modified:
                changes += [update_object(tm1_service=tm1_service, object_instance=m) for m in self.modified]

            if self.removed:
                changes += [delete_object(tm1_service=tm1_service, object_instance=d).url for d in self.removed]

        return changes


    def export(self, file_path: Union[str, Path]) -> Path:

        output_path = Path(file_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"changes": self.changes}, indent=2), encoding="utf-8")
        return output_path


    def sort(self):
        flag_precedence = {'C': 0, 'U': 1, 'D': 2}
        object_precedence = {'dimensions': 0, 'hierarchies': 1, 'subsets': 2, 'cubes': 3, 'process': 4, 'chore': 5}

        def __sort_changes(s: str):
            changes_precedence = {'dimensions': 0, 'hierarchies': 1, 'subsets': 2, 'cubes': 3, 'process': 4, 'chore': 5}

            flag = re.search(r'\A([UDC])', s).group(1)
            obj_name = re.search(r'/\b(\w*)/', s).group(1)

            if 'subsets' in s:
                obj_name = 'subsets'
            elif 'hierarchies' in s:
                obj_name = 'hierarchies'

            source_path = s.split(obj_name)[1]

            if flag == 'D':
                changes_precedence = {'cubes': 0, 'subsets': 1, 'hierarchies': 2, 'dimensions': 3, 'chore': 4, 'process': 5}

            key = (
                flag_precedence.get(flag, 99),
                changes_precedence.get(obj_name, 99),
                source_path
            )

            return key

        def __sort_on_source_path(s: T | Dict[T, Any]):

            if isinstance(s, (Cube, Dimension, Hierarchy, Subset, Chore, Process, MDXView)):
                s = s.source_path
            else:
                s = s["new"].source_path

            obj_name = re.search(r'\A(\w*)/', s).group(1)

            if 'subsets' in s:
                obj_name = 'subsets'
            elif 'hierarchies' in s:
                obj_name = 'hierarchies'

            source_path = s.split(obj_name)[1]

            key = (
                object_precedence.get(obj_name, 99),
                source_path
            )

            return key

        if self.has_changes():
            self.changes.sort(key=__sort_changes)
            self.added.sort(key=__sort_on_source_path)
            self.modified.sort(key=__sort_on_source_path)
            if self.removed:
                object_precedence = {'cubes': 0, 'subsets': 1, 'hierarchies': 2, 'dimensions': 3, 'chore': 4, 'process': 5}
                self.removed.sort(key=__sort_on_source_path)


def import_changeset(model1: Model, model2: Model, changeset_file) -> Changeset:
    """
    Build a Changeset instance from a lightweight export file and two models.
    The file is expected to contain {"changes": [...]} entries produced by Changeset.export().
    """
    payload = _load_changeset_payload(changeset_file)
    entries = payload.get("changes", [])
    if not isinstance(entries, list):
        raise ValueError("changeset payload must contain a list under 'changes'")

    old_index = _build_path_index(model1)
    new_index = _build_path_index(model2)

    changeset = Changeset()
    changeset.changes = []

    for raw in entries:
        if not isinstance(raw, str) or not raw:
            continue

        flag = raw[0]
        path_match = re.search(r"/(.+)$", raw)
        if not path_match:
            continue

        rel_path = path_match.group(1).strip()
        old_obj = _resolve_path(rel_path, old_index)
        new_obj = _resolve_path(rel_path, new_index)

        if flag == "C":
            if new_obj:
                changeset.added.append(new_obj)
        elif flag == "D":
            if old_obj:
                changeset.removed.append(old_obj)
        elif flag == "U":
            if old_obj and new_obj:
                changeset.modified.append({
                    "old": old_obj,
                    "new": new_obj,
                    "changes": f"Content of {new_obj.__class__.__name__} '{getattr(new_obj, 'name', '')}' changed."
                })

        #changeset.changes.append(raw)

    return changeset



# --------------------------------------------------------------------------------
# CRUD operations for apply changeset function
# --------------------------------------------------------------------------------

def create_object(tm1_service: TM1Service, object_instance: T) -> Response:
    if type(object_instance) is Dimension:
        return create_dimension(tm1_service=tm1_service, dimension=object_instance)

    elif type(object_instance) is Hierarchy:
        return create_hierarchy(tm1_service=tm1_service, hierarchy=object_instance)

    elif type(object_instance) is Subset:
        return create_subset(tm1_service=tm1_service, subset=object_instance)

    elif type(object_instance) is Cube:
        return create_cube(tm1_service=tm1_service, cube=object_instance)

    elif type(object_instance) is Process:
        return create_process(tm1_service=tm1_service, process=object_instance)

    elif type(object_instance) is Chore:
        return create_chore(tm1_service=tm1_service, chore=object_instance)

    else: raise ValueError


def delete_object(tm1_service: TM1Service, object_instance: T) -> Response:
    if type(object_instance) is Cube:
        return delete_cube(tm1_service=tm1_service, cube_name=object_instance.name)

    elif type(object_instance) is Subset:
        return delete_subset(tm1_service=tm1_service, subset=object_instance)

    elif type(object_instance) is Hierarchy:
        return delete_hierarchy(tm1_service=tm1_service, hierarchy=object_instance)

    elif type(object_instance) is Dimension:
        return delete_dimension(tm1_service=tm1_service, dimension_name=object_instance.name)

    elif type(object_instance) is Chore:
        return delete_chore(tm1_service=tm1_service, chore=object_instance.name)

    elif type(object_instance) is Process:
        return delete_process(tm1_service=tm1_service, process=object_instance.name)

    else:
        raise ValueError


def update_object(tm1_service: TM1Service, object_instance: Dict[T, Any]) -> Response:
    if type(object_instance['new']) is Dimension:
        return update_dimension(tm1_service=tm1_service, dimension=object_instance)

    elif type(object_instance['new']) is Hierarchy:
        return update_hierarchy(tm1_service=tm1_service, hierarchy=object_instance)

    elif type(object_instance['new']) is Subset:
        return update_subset(tm1_service=tm1_service, subset=object_instance)

    elif type(object_instance['new']) is Cube:
        return update_cube(tm1_service=tm1_service, cube=object_instance)

    elif type(object_instance['new']) is Process:
        return update_process(tm1_service=tm1_service, process=object_instance)

    elif type(object_instance['new']) is Chore:
        return update_chore(tm1_service=tm1_service, chore=object_instance)

    else:
        raise ValueError


# --------------------------------------------------------------------------------
# Utility for import
# --------------------------------------------------------------------------------

def _load_changeset_payload(changeset_file) -> Dict[str, Any]:
    if hasattr(changeset_file, "read"):
        return json.load(changeset_file)

    with open(changeset_file, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _build_path_index(model: Model) -> Dict[str, Any]:
    index: Dict[str, Any] = {}
    for collection in (model.cubes, model.dimensions, model.processes, model.chores):
        for obj in collection:
            _index_object_paths(index, obj)
    return index


def _resolve_path(path_fragment: str, index: Dict[str, Any]) -> Optional[Any]:
    cleaned = path_fragment.lstrip("/")
    if cleaned in index:
        return index[cleaned]
    if cleaned.endswith(".json"):
        trimmed = cleaned[:-5]
        return index.get(trimmed)
    with_suffix = f"{cleaned}.json"
    return index.get(with_suffix)


def _index_object_paths(index: Dict[str, Any], obj: Any) -> None:
    source_path = getattr(obj, "source_path", None)
    if source_path:
        normalized = source_path.replace("\\", "/").lstrip("/")
        index[normalized] = obj
        if normalized.endswith(".json"):
            index[normalized[:-5]] = obj

    child_attrs = _CHILD_RELATIONS.get(type(obj), [])
    for attr in child_attrs:
        children = getattr(obj, attr, None)
        if not children:
            continue
        for child in children:
            _index_object_paths(index, child)
