import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, TypeVar, Union

from requests import Response

from tm1_git_py.changeset_status import ChangeSetStatusStore
from tm1_git_py.model import MDXView
from tm1_git_py.model.cube import Cube, create_cube, update_cube, delete_cube
from tm1_git_py.model.dimension import Dimension, create_dimension, update_dimension, delete_dimension
from tm1_git_py.model.hierarchy import Hierarchy, create_hierarchy, update_hierarchy, delete_hierarchy
from tm1_git_py.model.mdxview import create_mdx_view, delete_mdx_view, update_mdx_view
from tm1_git_py.model.subset import Subset, create_subset, update_subset, delete_subset
from tm1_git_py.model.process import Process, create_process, update_process, delete_process
from tm1_git_py.model.chore import Chore, create_chore, update_chore, delete_chore
from TM1py import TM1Service

from tm1_git_py.model.model import Model

logger = logging.getLogger(__name__)


T = TypeVar('T', Cube, Dimension, Process, Chore)

_CHILD_RELATIONS: Dict[type, List[str]] = {
    Dimension: ["hierarchies"],
    Hierarchy: ["subsets", "edges", "elements"],
    Cube: ["views"],
}

FLAG_PRECEDENCE = {"C": 0, "U": 1, "D": 2}
OBJECT_PRECEDENCE = {'dimensions': 0, 'hierarchies': 1, 'subsets': 2, 'cubes': 3, 'views': 4, 'processes': 5, 'chores': 6}
DELETE_OBJECT_PRECEDENCE = {'views': 0, 'cubes': 1, 'subsets': 2, 'hierarchies': 3, 'dimensions': 4, 'chores': 5, 'processes': 6}


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


def _source_path_sort_key(s: T | Dict[T, Any], delete_precedence = False):
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


class Changeset:

    model: Model

    def __init__(self):

        self.added: List[T] = []
        self.modified: List[Dict[T, Any]] = []
        self.removed: List[T] = []

        self.changes: List[str] = []
        self.last_execution_id: str = '0'

    @property
    def all_removed(self) -> List[str]:
        return self.removed

    @property
    def lines(self) -> list[str]:
        return self._build_changes()

    def __repr__(self):
        changes = self.lines
        if not changes:
            return "No changes"
        return "Changeset:\n" + "\n".join(changes)

    def add_created(self, obj: Any, *, message: str | None = None) -> None:
        self.added.append(obj)

    def add_deleted(self, obj: Any, *, message: str | None = None) -> None:
        self.removed.append(obj)

    def add_modified(
            self,
            old: Any,
            new: Any,
            *,
            changes: str | None = None,
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
        status_dir: str | Path | None = None,
        execution_id: str | None = None,
        changeset_name: str | None = None,
        fail_fast: bool = True,
        **kwargs
    ) -> tuple[bool, list[str]]:

        changes = []
        if not self.has_changes():
            logger.info("No changes to apply.")
            return True, changes

        self.sort()

        operations: List[tuple[str, Any]] = []
        operations += [("CREATE", obj) for obj in self.added]
        operations += [("UPDATE", obj) for obj in self.modified]
        operations += [("DELETE", obj) for obj in self.removed]

        store: ChangeSetStatusStore | None = None
        if status_dir is not None:
            store = ChangeSetStatusStore(status_dir=status_dir, execution_id=execution_id,
                                         changeset_name=changeset_name)
            store.start(total_operations=len(operations))
            self.last_execution_id = store.execution_id
            logger.info("changeset execution_id=%s status_file=%s", store.execution_id, store.path)

        def _obj_meta(o: Any) -> tuple[str, str | None, str | None]:
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
        output_path = Path(file_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"changes": self.lines}, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------------
# Import changeset function & helpers
# --------------------------------------------------------------------------------

def import_changeset(model1: Model, model2: Model, changeset_file) -> Changeset:
    try:
        payload = _load_changeset_payload(changeset_file)
    except Exception as exc:
        logger.error("Failed to load changeset payload from '%s': %s", changeset_file, exc)
        raise

    entries = payload.get("changes", [])
    if not isinstance(entries, list):
        raise ValueError("changeset payload must contain a list under 'changes'")

    old_index = _build_path_index(model1)
    new_index = _build_path_index(model2)

    changeset = Changeset()

    for raw in entries:
        if not isinstance(raw, str):
            continue
        line = raw.strip()
        if not line:
            continue

        parts = line.split(None, 2)
        if len(parts) < 2:
            continue

        flag, path_part = parts[0], parts[1]

        rel_path = path_part.lstrip("/")

        old_obj = _resolve_path(rel_path, old_index)
        new_obj = _resolve_path(rel_path, new_index)

        if flag == "C":
            if new_obj is not None:
                changeset.add_created(new_obj)
        elif flag == "D":
            if old_obj is not None:
                changeset.add_deleted(old_obj)
        elif flag == "U":
            if old_obj is not None and new_obj is not None:
                changeset.add_modified(
                    old=old_obj,
                    new=new_obj,
                    changes=f"Content of {new_obj.__class__.__name__} "
                            f"'{getattr(new_obj, 'name', '')}' changed."
                )
    changeset.sort()

    return changeset


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


def _resolve_path(path: str, index: Dict[str, Any]) -> Any:
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


def _index_object_paths(index: Dict[str, Any], obj: Any) -> None:
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


def update_object(tm1_service: TM1Service, object_instance: Dict[T, Any], **kwargs) -> Response:
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
