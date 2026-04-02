import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Literal, Union

from tm1_git_py.changeset import Changeset, Change, ChangeType, ObjectType
from tm1_git_py.model import Hierarchy, MDXView, NativeView, Subset, Element, Edge, Rule
from tm1_git_py.model.store_backed_sequence import StoreBackedSequence
from tm1_git_py.model.chore import Chore
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.model import Model
from tm1_git_py.model.process import Process
from tm1_git_py.filter import filter

logger = logging.getLogger(__name__)


@dataclass
class _CompareObjectListsResult:
    matched_pairs: dict[str, tuple[Any, Any]]
    added_items: list[Any]
    removed_items: list[Any]


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


def _uri_from_object(obj: Any, context: Optional[dict[str, str]] = None) -> str:
    context = context or {}
    try:
        if isinstance(obj, Rule):
            cube_name = context.get("cube_name")
            return obj.uri(cube_name) if cube_name else ""
        if isinstance(obj, (MDXView, NativeView)):
            cube_name = context.get("cube_name")
            return obj.uri(cube_name) if cube_name else ""
        if isinstance(obj, Hierarchy):
            dimension_name = context.get("dimension_name")
            return obj.uri(dimension_name) if dimension_name else ""
        if isinstance(obj, (Subset, Element, Edge)):
            dimension_name = context.get("dimension_name")
            hierarchy_name = context.get("hierarchy_name")
            return obj.uri(dimension_name, hierarchy_name) if dimension_name and hierarchy_name else ""
        return obj.uri()
    except Exception:
        return ""


def _resolve_change_uri(obj: Any, context: Optional[dict[str, str]] = None) -> str:
    object_uri = _uri_from_object(obj, context=context)
    return object_uri or ""


def _object_identity(obj: Any, context: Optional[dict[str, str]] = None) -> str:
    obj_type = obj.__class__.__name__
    if isinstance(obj, Rule):
        object_uri = _uri_from_object(obj, context=context)
        area = getattr(obj, "area", "")
        if object_uri:
            return f"{obj_type}:{object_uri}|{area}"
        return f"{obj_type}:{getattr(obj, 'name', '')}:{area}"

    object_uri = _uri_from_object(obj, context=context)
    if object_uri:
        return f"{obj_type}:{object_uri}"

    if isinstance(obj, Edge):
        return f"{obj_type}:{getattr(obj, 'parent', '')}:{getattr(obj, 'name', '')}"

    name = getattr(obj, "name", None)
    if name is not None:
        return f"{obj_type}:{name}"

    uri_fn = getattr(obj, "uri", None)
    if callable(uri_fn):
        try:
            uri = uri_fn()
        except Exception:
            uri = None
        if uri:
            return f"{obj_type}:{uri}"
    raise AttributeError(f"Object '{obj}' has neither uri nor name.")


def _normalize_filter(
        filter_rules: Optional[Union[list[str], dict]] = None
) -> list[str]:
    def normalize_rule_prefix(s):
        if not s:
            return s
        if s[0] == "+":
            return s[1:]
        if s[0] == "-":
            return s[1:]
        return s

    filter_rules_lines = []
    if isinstance(filter_rules, list):
        filter_rules_lines += [normalize_rule_prefix(f) for f in filter_rules]
        return filter_rules_lines

    if isinstance(filter_rules, dict):
        if filter_rules.get("added"):
            filter_rules_lines += [normalize_rule_prefix(f) for f in filter_rules.get("added")]
        if filter_rules.get("modified"):
            filter_rules_lines += [normalize_rule_prefix(f) for f in filter_rules.get("modified")]
        if filter_rules.get("removed"):
            filter_rules_lines += [normalize_rule_prefix(f) for f in filter_rules.get("removed")]
        return filter_rules_lines

    else:
        raise ValueError("Invalid filter format for Comparator.")


class Comparator:
    DISK_BACKED_PROGRESS_EVERY: int = 100_000
    LOCAL_SORT_CHUNK_SIZE: int = 100_000

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

        logger.info("Starting model compare mode=%s", mode)
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
            uri: str = "",
    ) -> None:
        changeset.changes.append(
            Change(
                change_type=change_type,
                object_type=ObjectType.from_object(obj),
                uri=uri,
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
            context: Optional[dict[str, str]] = None,
    ) -> _CompareObjectListsResult:

        equals_fn = self._EQUALITY_OVERRIDES.get(parent_cls)
        object_type_name = getattr(parent_cls, "__name__", str(parent_cls))

        compare_result = self._compare_object_lists(
            old_list,
            new_list,
            changeset,
            object_type_name=object_type_name,
            mode=mode,
            equals_fn=equals_fn,
            context=context,
            parent_cls=parent_cls,
        )

        child_relations = self._CHILD_RELATIONS.get(parent_cls, [])
        parent_pairs = compare_result.matched_pairs
        if child_relations and parent_pairs:
            for old_obj, new_obj in parent_pairs.values():
                for child_attr, child_cls in child_relations:
                    # "Leaves" hierarchy elements are auto-managed by TM1 and should not be diffed.
                    if isinstance(new_obj, Hierarchy) and child_attr == "elements" and _is_leaf_hierarchy(new_obj):
                        continue
                    slot_old = getattr(old_obj, child_attr, None) or []
                    slot_new = getattr(new_obj, child_attr, None) or []
                    if isinstance(slot_old, StoreBackedSequence) and isinstance(slot_new, StoreBackedSequence):
                        old_children = slot_old
                        new_children = slot_new
                    else:
                        old_children = [
                            child for child in slot_old
                            if isinstance(child, child_cls)
                        ]
                        new_children = [
                            child for child in slot_new
                            if isinstance(child, child_cls)
                        ]
                    try:
                        child_context = dict(context or {})
                        if parent_cls is Cube:
                            child_context["cube_name"] = getattr(new_obj, "name", "")
                        if parent_cls is Dimension:
                            child_context["dimension_name"] = getattr(new_obj, "name", "")
                        if parent_cls is Hierarchy:
                            child_context["hierarchy_name"] = getattr(new_obj, "name", "")
                        self._compare_with_children(old_children, new_children, child_cls, changeset, mode, context=child_context)
                    except Exception as exc:
                        logger.error(
                            "Child comparison failed for relation '%s' of %s: %s",
                            child_attr,
                            object_type_name,
                            exc,
                            exc_info=True,
                        )
                        raise

        if child_relations and compare_result.added_items:
            for new_obj in compare_result.added_items:
                for child_attr, child_cls in child_relations:
                    if isinstance(new_obj, Hierarchy) and child_attr == "elements" and _is_leaf_hierarchy(new_obj):
                        continue
                    slot_new = getattr(new_obj, child_attr, None) or []
                    if isinstance(slot_new, StoreBackedSequence):
                        new_children = slot_new
                    else:
                        new_children = [
                            child for child in slot_new
                            if isinstance(child, child_cls)
                        ]
                    child_context = dict(context or {})
                    if parent_cls is Cube:
                        child_context["cube_name"] = getattr(new_obj, "name", "")
                    if parent_cls is Dimension:
                        child_context["dimension_name"] = getattr(new_obj, "name", "")
                    if parent_cls is Hierarchy:
                        child_context["hierarchy_name"] = getattr(new_obj, "name", "")
                    self._compare_with_children([], new_children, child_cls, changeset, mode, context=child_context)

        if mode == "full" and child_relations and compare_result.removed_items:
            for old_obj in compare_result.removed_items:
                for child_attr, child_cls in child_relations:
                    if isinstance(old_obj, Hierarchy) and child_attr == "elements" and _is_leaf_hierarchy(old_obj):
                        continue
                    slot_old = getattr(old_obj, child_attr, None) or []
                    if isinstance(slot_old, StoreBackedSequence):
                        old_children = slot_old
                    else:
                        old_children = [
                            child for child in slot_old
                            if isinstance(child, child_cls)
                        ]
                    child_context = dict(context or {})
                    if parent_cls is Cube:
                        child_context["cube_name"] = getattr(old_obj, "name", "")
                    if parent_cls is Dimension:
                        child_context["dimension_name"] = getattr(old_obj, "name", "")
                    if parent_cls is Hierarchy:
                        child_context["hierarchy_name"] = getattr(old_obj, "name", "")
                    self._compare_with_children(old_children, [], child_cls, changeset, mode, context=child_context)
        return compare_result

    def _compare_disk_backed_sorted_merge(
            self,
            old_db: Any,
            new_db: Any,
            changeset: Changeset,
            *,
            object_type_name: str,
            mode: Literal['full', 'add_only'],
            equals_fn: Optional[Callable[[Any, Any], bool]],
            context: Optional[dict[str, str]],
    ) -> _CompareObjectListsResult:
        """
        Compare two on-disk JSONL collections in merge order.

        Callers must ensure both sides iterate in ascending order by
        ``_object_identity(..., context)`` (same order as TM1/export queries).
        """
        old_it = (old_db.item_from_payload(payload) for payload in old_db.iter_payloads(ordered_by_identity=True))
        new_it = (new_db.item_from_payload(payload) for payload in new_db.iter_payloads(ordered_by_identity=True))
        old_item: Any = next(old_it, None)
        new_item: Any = next(new_it, None)
        added_c = removed_c = common_c = 0
        old_total = len(old_db)
        new_total = len(new_db)
        old_seen = 0
        new_seen = 0
        progress_every = max(1, self.DISK_BACKED_PROGRESS_EVERY)
        next_log_at = progress_every
        old_signature = old_db.sidecar_content_signature()
        new_signature = new_db.sidecar_content_signature()

        if old_signature and new_signature and old_signature == new_signature:
            logger.info(
                "Skipping %s streaming compare: count+hash match count=%d hash_algo=%s",
                object_type_name,
                old_signature[0],
                getattr(old_db, "HASH_ALGO", "sha256-chain-v1"),
            )
            return _CompareObjectListsResult(matched_pairs={}, added_items=[], removed_items=[])

        logger.info(
            "Starting %s streaming compare old_size=%d new_size=%d progress_every=%d",
            object_type_name,
            old_total,
            new_total,
            progress_every,
        )

        def _log_progress(force: bool = False) -> None:
            nonlocal next_log_at
            current = max(old_seen, new_seen)
            should_log = force or current >= next_log_at
            if not should_log:
                return
            logger.info(
                "Streaming compare progress for %s old=%d/%d new=%d/%d added=%d removed=%d common=%d",
                object_type_name,
                old_seen,
                old_total,
                new_seen,
                new_total,
                added_c,
                removed_c,
                common_c,
            )
            while current >= next_log_at:
                next_log_at += progress_every

        while old_item is not None or new_item is not None:
            if old_item is None:
                self._append_change(
                    changeset,
                    change_type=ChangeType.ADD,
                    obj=new_item,
                    uri=_resolve_change_uri(new_item, context),
                )
                added_c += 1
                new_item = next(new_it, None)
                new_seen += 1
                _log_progress()
                continue
            if new_item is None:
                if mode == 'full':
                    self._append_change(
                        changeset,
                        change_type=ChangeType.REMOVE,
                        obj=old_item,
                        uri=_resolve_change_uri(old_item, context),
                    )
                    removed_c += 1
                old_item = next(old_it, None)
                old_seen += 1
                _log_progress()
                continue

            try:
                key_old = _object_identity(old_item, context=context)
                key_new = _object_identity(new_item, context=context)
            except AttributeError as exc:
                logger.error(
                    "Objects missing identity fields in %s streaming compare: %s",
                    object_type_name,
                    exc,
                    exc_info=True,
                )
                raise

            if key_old < key_new:
                if mode == 'full':
                    self._append_change(
                        changeset,
                        change_type=ChangeType.REMOVE,
                        obj=old_item,
                        uri=_resolve_change_uri(old_item, context),
                    )
                    removed_c += 1
                old_item = next(old_it, None)
                old_seen += 1
                _log_progress()
            elif key_old > key_new:
                self._append_change(
                    changeset,
                    change_type=ChangeType.ADD,
                    obj=new_item,
                    uri=_resolve_change_uri(new_item, context),
                )
                added_c += 1
                new_item = next(new_it, None)
                new_seen += 1
                _log_progress()
            else:
                common_c += 1
                try:
                    objects_equal = equals_fn(old_item, new_item) if equals_fn else old_item == new_item
                    if not objects_equal:
                        self._append_change(
                            changeset,
                            change_type=ChangeType.MODIFY,
                            obj=new_item,
                            uri=_resolve_change_uri(new_item, context),
                        )
                except Exception as exc:
                    logger.error(
                        "Failed comparing %s '%s': %s",
                        object_type_name,
                        key_old,
                        exc,
                        exc_info=True,
                    )
                    raise
                old_item = next(old_it, None)
                new_item = next(new_it, None)
                old_seen += 1
                new_seen += 1
                _log_progress()

        _log_progress(force=True)
        logger.debug(
            "Diff counts for %s (streaming): added=%d removed=%d common=%d",
            object_type_name,
            added_c,
            removed_c,
            common_c,
        )
        return _CompareObjectListsResult(matched_pairs={}, added_items=[], removed_items=[])

    def _compare_object_lists(self,
                              old_list: Iterable[Any],
                              new_list: Iterable[Any],
                              changeset: Changeset,
                              object_type_name: str,
                              mode: Literal['full', 'add_only'],
                              equals_fn: Optional[Callable[[Any, Any], bool]] = None,
                              context: Optional[dict[str, str]] = None,
                              parent_cls: Optional[type] = None) -> _CompareObjectListsResult:

        if (
            parent_cls in (Element, Edge)
            and isinstance(old_list, StoreBackedSequence)
            and isinstance(new_list, StoreBackedSequence)
        ):
            return self._compare_disk_backed_sorted_merge(
                old_list,
                new_list,
                changeset,
                object_type_name=object_type_name,
                mode=mode,
                equals_fn=equals_fn,
                context=context,
            )

        try:
            old_list_m = list(old_list)
            new_list_m = list(new_list)
            old_map = {_object_identity(obj, context=context): obj for obj in old_list_m}
            new_map = {_object_identity(obj, context=context): obj for obj in new_list_m}
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
                obj=new_map[name],
                uri=_resolve_change_uri(new_map[name], context),
            )

        if mode == 'full':
            for name in removed_names:
                self._append_change(
                    changeset,
                    change_type=ChangeType.REMOVE,
                    obj=old_map[name],
                    uri=_resolve_change_uri(old_map[name], context),
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
                        obj=new_obj,
                        uri=_resolve_change_uri(new_obj, context),
                    )
            except Exception as exc:
                logger.error("Failed comparing %s '%s': %s", object_type_name, name, exc, exc_info=True)
                raise

        return _CompareObjectListsResult(
            matched_pairs=matched_pairs,
            added_items=[new_map[name] for name in added_names],
            removed_items=[old_map[name] for name in removed_names],
        )

