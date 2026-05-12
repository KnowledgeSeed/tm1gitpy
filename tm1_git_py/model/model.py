from typing import List, Any, Dict, Iterable, Optional
from itertools import chain

from .cube import Cube
from .dimension import Dimension
from .chore import Chore
from .process import Process
from .hierarchy import Hierarchy
from .subset import Subset
from .element import Element
from .edge import Edge
from .mdxview import MDXView
from .nativeview import NativeView
from .rule import Rule
from .store_backed_sequence import StoreBackedSequence


class Model:
    def __init__(
        self,
        cubes: List[Cube],
        dimensions: List[Dimension],
        processes: List[Process],
        chores: List[Chore],
        *,
        model_id: str = "default",
        total_object_count: Optional[int] = None,
    ):
        self.cubes = cubes
        self.dimensions = dimensions
        self.processes = processes
        self.chores = chores
        self.model_id = str(model_id or "default")
        self.total_object_count = int(total_object_count) if total_object_count is not None else None

    def to_dict(self):
        return {
            'cubes': [c.to_dict() for c in self.cubes],
            'dimensions': [d.to_dict() for d in self.dimensions],
            'processes': [p.to_dict() for p in self.processes],
            'chores': [c.to_dict() for c in self.chores]
        }

    def get_all_objects_with_uris(self) -> Dict[str, Any]:
        from tm1_git_py.services.filter import normalize_for_path

        all_objects: Dict[str, Any] = {}

        for item in chain(self.processes, self.dimensions, self.cubes):
            item_uri_fn = getattr(item, "uri", None)
            item_uri = item_uri_fn() if callable(item_uri_fn) else None
            if item_uri:
                all_objects[item_uri] = item

        for cube in self.cubes:
            for rule in cube.rules:
                rule_uri_fn = getattr(rule, "uri", None)
                rule_uri = rule_uri_fn(cube.name) if callable(rule_uri_fn) else None
                if rule_uri:
                    # Keep rule-area granularity while using URI as the baseline identity.
                    all_objects[f"{rule_uri}|{normalize_for_path(getattr(rule, 'area', ''))}"] = rule
            for view in cube.views:
                view_uri_fn = getattr(view, "uri", None)
                view_uri = view_uri_fn(cube.name) if callable(view_uri_fn) else None
                if view_uri:
                    all_objects[view_uri] = view

        for dimension in self.dimensions:
            for hierarchy in dimension.hierarchies:
                hierarchy_uri_fn = getattr(hierarchy, "uri", None)
                hierarchy_uri = hierarchy_uri_fn(dimension.name) if callable(hierarchy_uri_fn) else None
                if hierarchy_uri:
                    all_objects[hierarchy_uri] = hierarchy

                for subset in getattr(hierarchy, "subsets", []) or []:
                    subset_uri_fn = getattr(subset, "uri", None)
                    subset_uri = subset_uri_fn(dimension.name, hierarchy.name) if callable(subset_uri_fn) else None
                    if subset_uri:
                        all_objects[subset_uri] = subset

                for element in getattr(hierarchy, "elements", []) or []:
                    element_uri_fn = getattr(element, "uri", None)
                    element_uri = element_uri_fn(dimension.name, hierarchy.name) if callable(element_uri_fn) else None
                    if element_uri:
                        all_objects[element_uri] = element

                for edge in getattr(hierarchy, "edges", []) or []:
                    edge_uri_fn = getattr(edge, "uri", None)
                    edge_uri = edge_uri_fn(dimension.name, hierarchy.name) if callable(edge_uri_fn) else None
                    if edge_uri:
                        all_objects[edge_uri] = edge

        for chore in self.chores:
            chore_uri_fn = getattr(chore, "uri", None)
            chore_uri = chore_uri_fn() if callable(chore_uri_fn) else None
            if chore_uri:
                all_objects[chore_uri] = chore

        return all_objects

    @classmethod
    def _count_collection_objects(cls, items: Iterable[Any], object_cls: type) -> int:
        child_relations: dict[type, list[tuple[str, type]]] = {
            Dimension: [("hierarchies", Hierarchy)],
            Hierarchy: [("subsets", Subset), ("elements", Element), ("edges", Edge)],
            Cube: [("views", MDXView), ("views", NativeView), ("rules", Rule)],
        }
        total = 0
        for obj in items:
            if not isinstance(obj, object_cls):
                continue
            total += 1
            for child_attr, child_cls in child_relations.get(object_cls, []):
                slot_items = getattr(obj, child_attr, None) or []
                if isinstance(slot_items, StoreBackedSequence):
                    total += len(slot_items)
                    continue
                total += cls._count_collection_objects(slot_items, child_cls)
        return total

    @classmethod
    def recalculate_total_object_count(cls, model: "Model") -> int:
        total = (
            cls._count_collection_objects(model.cubes, Cube)
            + cls._count_collection_objects(model.dimensions, Dimension)
            + cls._count_collection_objects(model.processes, Process)
            + cls._count_collection_objects(model.chores, Chore)
        )
        model.total_object_count = int(total)
        return int(total)