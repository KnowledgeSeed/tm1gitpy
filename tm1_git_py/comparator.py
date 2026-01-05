from collections.abc import Callable, Iterable, Mapping
import logging
from typing import List, Dict, Any, Optional, Tuple
from tm1_git_py.changeset import Changeset
from tm1_git_py.model import Hierarchy, Subset, MDXView, Dimension, Cube, Process, Chore, Model

logger = logging.getLogger(__name__)

def _dimensions_equal_shallow(old_dimension: Dimension, new_dimension: Dimension) -> bool:
    try:
        if old_dimension.name != new_dimension.name:
            return False

        old_hierarchy_names = {hier.name for hier in old_dimension.hierarchies}
        new_hierarchy_names = {hier.name for hier in new_dimension.hierarchies}
        if old_hierarchy_names != new_hierarchy_names:
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
        if old_hierarchy.name != new_hierarchy.name:
            return False

        old_element_names = {element.name for element in old_hierarchy.elements}
        new_element_names = {element.name for element in new_hierarchy.elements}
        if old_element_names != new_element_names:
            return False

        old_edge_names = {edge.name for edge in old_hierarchy.edges}
        new_edge_names = {edge.name for edge in new_hierarchy.edges}
        if old_edge_names != new_edge_names:
            return False

        old_subset_names = {subset.name for subset in old_hierarchy.subsets}
        new_subset_names = {subset.name for subset in new_hierarchy.subsets}
        if old_subset_names != new_subset_names:
            return False

        return True

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

        old_view_names = {view.name for view in old_cube.views}
        new_view_names = {view.name for view in new_cube.views}
        if old_view_names != new_view_names:
            return False

        if set(old_cube.rules) != set(new_cube.rules):
            return False

        return True

    except AttributeError as exc:
        logger.error("Cube comparison failed due to missing attributes: %s", exc)
        return False
class Comparator:
    _CHILD_RELATIONS: Mapping[type, List[Tuple[str, type]]] = {
        Dimension: [("hierarchies", Hierarchy)],
        Hierarchy: [("subsets", Subset)],
        Cube: [("views", MDXView)],
    }

    _EQUALITY_OVERRIDES: Mapping[type, Callable[[Any, Any], bool]] = {
        Dimension: _dimensions_equal_shallow,
        Hierarchy: _hierarchies_equal_shallow,
        Cube: _cubes_equal_shallow
    }

    def compare(self, model1: Model, model2: Model, mode: str = 'full') -> Changeset:
        """
        Comparison:
            model1: Old model.
            model2: New model.
            mode: The mode of the comparison 'full' (stores every change)
                  or 'add_only' ( only stores the added and modified objects)
        """

        changeset = Changeset()

        self._compare_with_children(model1.cubes, model2.cubes, Cube, changeset, mode)
        self._compare_with_children(model1.dimensions, model2.dimensions, Dimension, changeset, mode)
        self._compare_with_children(model1.processes, model2.processes, Process, changeset, mode)
        self._compare_with_children(model1.chores, model2.chores, Chore, changeset, mode)

        return changeset


    def _compare_with_children(
            self,
            old_list: Iterable[Any],
            new_list: Iterable[Any],
            parent_cls: type,
            changeset: Changeset,
            mode: str,
    ) -> Dict[str, Tuple[Any, Any]]:

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
                    old_children = getattr(old_obj, child_attr, None) or []
                    new_children = getattr(new_obj, child_attr, None) or []
                    try:
                        self._compare_with_children(old_children, new_children, child_cls, changeset, mode)
                    except Exception as exc:
                        logger.error("Child comparison failed for relation '%s' of %s: %s", child_attr, object_type_name, exc)
                        raise

        return parent_pairs

    def _compare_object_lists(self,
                              old_list: List[Any],
                              new_list: List[Any],
                              changeset: Changeset,
                              object_type_name: str,
                              mode: str,
                              equals_fn: Optional[Callable[[Any, Any], bool]] = None) -> Dict[str, Tuple[Any, Any]]:

        try:
            old_map = {obj.name: obj for obj in old_list}
            new_map = {obj.name: obj for obj in new_list}
        except AttributeError as exc:
            logger.error("Objects missing 'name' attribute in %s comparison: %s", object_type_name, exc)
            raise

        new_names = set(new_map.keys())
        old_names = set(old_map.keys())

        added_names = new_names - old_names
        for name in added_names:
            changeset.add_created(new_map[name])

        if mode == 'full':
            removed_names = old_names - new_names
            for name in removed_names:
                changeset.add_deleted(old_map[name])

        common_names = new_names & old_names
        matched_pairs: Dict[str, Tuple[Any, Any]] = {}
        for name in common_names:
            try:
                old_obj = old_map[name]
                new_obj = new_map[name]
                matched_pairs[name] = (old_obj, new_obj)
                objects_equal = equals_fn(old_obj, new_obj) if equals_fn else old_obj == new_obj
                if not objects_equal:
                    changeset.add_modified(old=old_obj, new=new_obj,
                                          changes=f"Content of {object_type_name} '{name}' changed.")
            except Exception as exc:
                logger.error("Failed comparing %s '%s': %s", object_type_name, name, exc)
                raise

        return matched_pairs
