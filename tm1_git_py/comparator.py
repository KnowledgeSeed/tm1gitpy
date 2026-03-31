import logging
from typing import Any, Callable, Iterable, Mapping, Optional, Literal, Union

from tm1_git_py.changeset import Changeset, Change, ChangeType, ObjectType
from tm1_git_py.model import Hierarchy, MDXView, NativeView, Subset, Element, Edge, Rule
from tm1_git_py.model.chore import Chore
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.model import Model
from tm1_git_py.model.process import Process
from tm1_git_py.filter import filter

logger = logging.getLogger(__name__)


def _dimensions_equal_shallow(old_dimension: Dimension, new_dimension: Dimension) -> bool:
    try:
        if old_dimension.name != new_dimension.name:
            return False

        old_default = getattr(old_dimension.defaultHierarchy, "name", None)
        new_default = getattr(new_dimension.defaultHierarchy, "name", None)
        if old_default != new_default:
            return False

        return True

    except AttributeError as exc:
        logger.error("Dimension comparison failed due to missing attributes: %s", exc)
        return False


def _hierarchies_equal_shallow(old_hierarchy: Hierarchy, new_hierarchy: Hierarchy) -> bool:
    try:
        return old_hierarchy.name == new_hierarchy.name

    except AttributeError as exc:
        logger.error("Hierarchy comparison failed due to missing attributes: %s", exc)
        return False


def _cubes_equal_shallow(old_cube: Cube, new_cube: Cube) -> bool:
    try:
        if old_cube.name != new_cube.name:
            return False

        old_dim_names = {dim.name for dim in old_cube.dimensions}
        new_dim_names = {dim.name for dim in new_cube.dimensions}
        if old_dim_names != new_dim_names:
            return False

        return True

    except AttributeError as exc:
        logger.error("Cube comparison failed due to missing attributes: %s", exc)
        return False


def _is_leaf_hierarchy(hierarchy_obj: Any) -> bool:
    return getattr(hierarchy_obj, "name", "").strip().lower() == "leaves"


def _object_identity(obj: Any) -> str:
    obj_type = obj.__class__.__name__
    if isinstance(obj, Edge):
        return f"{obj_type}:{getattr(obj, 'parent', '')}:{getattr(obj, 'name', '')}"
    if isinstance(obj, Rule):
        return f"{obj_type}:{getattr(obj, 'name', '')}:{getattr(obj, 'area', '')}"

    name = getattr(obj, "name", None)
    if name is not None:
        return f"{obj_type}:{name}"

    source_path = getattr(obj, "source_path", None)
    if source_path:
        return f"{obj_type}:{source_path}"
    raise AttributeError(f"Object '{obj}' has neither source_path nor name.")


def _normalize_filter(
        filter_rules: Optional[Union[list[str], dict]] = None
) -> list[str]:
    def ensure_prefix(s):
        return s if s.startswith('-/') else '-/' + s

    filter_rules_lines = []
    if isinstance(filter_rules, list):
        filter_rules_lines += [ensure_prefix(f) for f in filter_rules]
        return filter_rules_lines

    if isinstance(filter_rules, dict):
        if filter_rules.get("added"):
            filter_rules_lines += [ensure_prefix(f) for f in filter_rules.get("added")]
        if filter_rules.get("modified"):
            filter_rules_lines += [ensure_prefix(f) for f in filter_rules.get("modified")]
        if filter_rules.get("removed"):
            filter_rules_lines += [ensure_prefix(f) for f in filter_rules.get("removed")]
        return filter_rules_lines

    else:
        raise ValueError("Invalid filter format for Comparator.")


class Comparator:
    DEFAULT_FILTER_RULES: list[str] = ["-/cubes/}*", "-/dimensions/}*"]

    _CHILD_RELATIONS: Mapping[type, list[tuple[str, type]]] = {
        Dimension: [("hierarchies", Hierarchy)],
        Hierarchy: [("subsets", Subset), ("elements", Element), ("edges", Edge)],
        Cube: [("views", MDXView), ("views", NativeView), ("rules", Rule)],
    }

    _EQUALITY_OVERRIDES: Mapping[type, Callable[[Any, Any], bool]] = {
        Dimension: _dimensions_equal_shallow,
        Hierarchy: _hierarchies_equal_shallow,
        Cube: _cubes_equal_shallow
    }

    def __init__(
            self,
            *,
            use_default_filter: bool = True
    ):
        self.use_default_filter = use_default_filter
        self.default_filter_rules = list(self.DEFAULT_FILTER_RULES)

    def compare(
            self,
            model1: Model,
            model2: Model,
            mode: Literal['full', 'add_only'] = 'full',
            filter_rules: Optional[Union[list[str], list[dict]]] = None
    ) -> Changeset:
        """
        Compare two models and build a Changeset of Change entries.
        mode='full' emits add/remove/modify changes.
        mode='add_only' emits add/modify changes.
        """

        logger.info(
            "Starting model compare mode=%s use_default_filter=%s",
            mode,
            self.use_default_filter,
        )
        logger.debug(
            "Input object counts old(cubes=%d dimensions=%d processes=%d chores=%d) "
            "new(cubes=%d dimensions=%d processes=%d chores=%d)",
            len(model1.cubes),
            len(model1.dimensions),
            len(model1.processes),
            len(model1.chores),
            len(model2.cubes),
            len(model2.dimensions),
            len(model2.processes),
            len(model2.chores),
        )

        if self.use_default_filter:
            logger.debug("Applying default comparator filters: %s", self.default_filter_rules)
            model1 = filter(model1, self.default_filter_rules)
            model2 = filter(model2, self.default_filter_rules)

        if filter_rules:
            if isinstance(filter_rules, list) and all(isinstance(i, str) for i in filter_rules):
                filter_rule = _normalize_filter(filter_rules)
                logger.debug("Applying comparator filter rules: %s", filter_rule)
                model1 = filter(model1, filter_rule)
                model2 = filter(model2, filter_rule)
            else:
                for filter_rule in filter_rules:
                    filter_rule = _normalize_filter(filter_rule)
                    logger.debug("Applying comparator filter rules: %s", filter_rule)
                    model1 = filter(model1, filter_rule)
                    model2 = filter(model2, filter_rule)

        changeset = Changeset()

        logger.debug("Comparing object type: Cube")
        self._compare_with_children(model1.cubes, model2.cubes, Cube, changeset, mode)
        logger.debug("Comparing object type: Dimension")
        self._compare_with_children(model1.dimensions, model2.dimensions, Dimension, changeset, mode)
        logger.debug("Comparing object type: Process")
        self._compare_with_children(model1.processes, model2.processes, Process, changeset, mode)
        logger.debug("Comparing object type: Chore")
        self._compare_with_children(model1.chores, model2.chores, Chore, changeset, mode)

        cube_rule_texts = {cube.name: cube.get_rule_text() for cube in model2.cubes}
        changeset.unify_rule_changes(cube_rule_texts=cube_rule_texts)
        changeset.sort()
        summary = {"add": 0, "remove": 0, "modify": 0}
        for change in changeset.changes:
            key = change.change_type.value if hasattr(change.change_type, "value") else str(change.change_type)
            summary[key] = summary.get(key, 0) + 1
        logger.info(
            "Completed model compare mode=%s total=%d add=%d remove=%d modify=%d",
            mode,
            len(changeset.changes),
            summary.get("add", 0),
            summary.get("remove", 0),
            summary.get("modify", 0),
        )

        return changeset

    @staticmethod
    def _append_change(
            changeset: Changeset,
            *,
            change_type: ChangeType,
            obj: Any,
    ) -> None:
        changeset.changes.append(
            Change(
                change_type=change_type,
                object_type=ObjectType.from_object(obj),
                source_path=getattr(obj, "source_path", ""),
                body=obj,
            )
        )


    def _compare_with_children(
            self,
            old_list: Iterable[Any],
            new_list: Iterable[Any],
            parent_cls: type,
            changeset: Changeset,
            mode: Literal['full', 'add_only'],
    ) -> dict[str, tuple[Any, Any]]:

        equals_fn = self._EQUALITY_OVERRIDES.get(parent_cls)
        object_type_name = getattr(parent_cls, "__name__", str(parent_cls))

        parent_pairs = self._compare_object_lists(
            list(old_list),
            list(new_list),
            changeset,
            object_type_name=object_type_name,
            mode=mode,
            equals_fn=equals_fn
        )

        child_relations = self._CHILD_RELATIONS.get(parent_cls, [])
        if child_relations and parent_pairs:
            for old_obj, new_obj in parent_pairs.values():
                for child_attr, child_cls in child_relations:
                    # "Leaves" hierarchy elements are auto-managed by TM1 and should not be diffed.
                    if isinstance(new_obj, Hierarchy) and child_attr == "elements" and _is_leaf_hierarchy(new_obj):
                        continue
                    old_children = [
                        child for child in (getattr(old_obj, child_attr, None) or [])
                        if isinstance(child, child_cls)
                    ]
                    new_children = [
                        child for child in (getattr(new_obj, child_attr, None) or [])
                        if isinstance(child, child_cls)
                    ]
                    try:
                        self._compare_with_children(old_children, new_children, child_cls, changeset, mode)
                    except Exception as exc:
                        logger.error(
                            "Child comparison failed for relation '%s' of %s: %s",
                            child_attr,
                            object_type_name,
                            exc,
                            exc_info=True,
                        )
                        raise

        return parent_pairs

    def _compare_object_lists(self,
                              old_list: list[Any],
                              new_list: list[Any],
                              changeset: Changeset,
                              object_type_name: str,
                              mode: Literal['full', 'add_only'],
                              equals_fn: Optional[Callable[[Any, Any], bool]] = None) -> dict[str, tuple[Any, Any]]:

        try:
            old_map = {_object_identity(obj): obj for obj in old_list}
            new_map = {_object_identity(obj): obj for obj in new_list}
        except AttributeError as exc:
            logger.error("Objects missing identity fields in %s comparison: %s", object_type_name, exc, exc_info=True)
            raise

        new_names = set(new_map.keys())
        old_names = set(old_map.keys())

        added_names = new_names - old_names
        removed_names = old_names - new_names
        common_names = new_names & old_names
        logger.debug(
            "Diff counts for %s: added=%d removed=%d common=%d",
            object_type_name,
            len(added_names),
            len(removed_names),
            len(common_names),
        )
        for name in added_names:
            self._append_change(
                changeset,
                change_type=ChangeType.ADD,
                obj=new_map[name]
            )

        if mode == 'full':
            for name in removed_names:
                self._append_change(
                    changeset,
                    change_type=ChangeType.REMOVE,
                    obj=old_map[name]
                )

        matched_pairs: dict[str, tuple[Any, Any]] = {}
        for name in common_names:
            try:
                old_obj = old_map[name]
                new_obj = new_map[name]
                matched_pairs[name] = (old_obj, new_obj)
                objects_equal = equals_fn(old_obj, new_obj) if equals_fn else old_obj == new_obj
                if not objects_equal:
                    self._append_change(
                        changeset,
                        change_type=ChangeType.MODIFY,
                        obj=new_obj
                    )
            except Exception as exc:
                logger.error("Failed comparing %s '%s': %s", object_type_name, name, exc, exc_info=True)
                raise

        return matched_pairs
