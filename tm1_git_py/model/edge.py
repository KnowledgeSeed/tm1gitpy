import json
import logging
import re
from typing import Any, Dict, Optional
from TM1py import TM1Service
from requests import Response

# {
#     "ParentName":"Provider Total",
#     "ComponentName":"ProviderTest",
#     "Weight":1
# }

class Edge:
    def __init__(
        self,
        parent,
        name,
        weight,
        *,
        source_path: Optional[str] = None,
        dimension_name: Optional[str] = None,
        hierarchy_name: Optional[str] = None
    ):
        self.parent = parent
        self.name = name
        self.weight = weight
        self.source_path = source_path or self.as_link(dimension_name, hierarchy_name, name, parent)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Edge):
            return NotImplemented
        return self.parent == other.parent and \
               self.name == other.name and \
               self.weight == other.weight

    def __hash__(self) -> int:
        return hash((self.parent, self.name, self.weight))

    def __repr__(self):
        return f"Edge('{self.name}')"

    def to_dict(self):
        return {
            'parentName': self.parent,
            'componentName': self.name,
            'weight': self.weight
        }

    def as_json(self):
        return json.dumps({
            "ParentName": self.parent,
            "ComponentName": self.name,
            "Weight": self.weight
        }, indent=4)

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        *,
        source_path: Optional[str] = None,
        dimension_name: Optional[str] = None,
        hierarchy_name: Optional[str] = None
    ) -> "Edge":
        parent = data.get("parentName") or data.get("parent") or data.get("ParentName")
        component = (
            data.get("componentName") or data.get("name") or data.get("ComponentName")
        )
        weight = data.get("weight")
        if weight is None:
            weight = data.get("Weight")
        if weight is None:
            weight = 1
        return cls(
            parent=parent,
            name=component,
            weight=weight,
            source_path=source_path or cls.as_link(dimension_name, hierarchy_name, component, parent)
        )

    @staticmethod
    def as_link(
        dimension_name_base: Optional[str],
        hierarchy_name_base: Optional[str],
        name: Optional[str],
        parent: Optional[str]
    ) -> Optional[str]:
        # dimensions/Dimension_A.hierarchies/Dimension_A.json/parent:component
        if dimension_name_base and hierarchy_name_base and name and parent:
            return f"dimensions/{dimension_name_base}.hierarchies/{hierarchy_name_base}.json/{parent}:{name}"
        return None


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def _edge_context_from_path(source_path: str) -> tuple[str, str]:
    normalized_path = (source_path or "").replace("\\", "/").lstrip("/")
    match = re.search(r"dimensions/([^/]+)\.hierarchies/([^/]+)\.json(?:/|$)", normalized_path)
    if not match:
        raise ValueError(f"Invalid edge source_path format: '{source_path}'")
    dimension_name, hierarchy_name = match.groups()
    return dimension_name, hierarchy_name


def create_edge(tm1_service: TM1Service, edge: Edge) -> Response:
    dimension, hierarchy = _edge_context_from_path(source_path=edge.source_path)
    edge_name = {(edge.parent, edge.name): edge.weight}
    logger.debug(f"Creating Edge: {edge.name} in Hierarchy: {hierarchy}.")
    return tm1_service.elements.add_edges(hierarchy, dimension, edge_name)


def update_edge(tm1_service: TM1Service, edge: Edge) -> Response:
    dimension, hierarchy = _edge_context_from_path(source_path=edge.source_path)
    hierarchy_object = tm1_service.hierarchies.get(dimension_name=dimension, hierarchy_name=hierarchy)
    hierarchy_object.update_edge(parent=edge.parent, component=edge.name, weight=edge.weight)
    resp = tm1_service.hierarchies.update(hierarchy_object)
    if isinstance(resp, list):
        resp = resp[0]
    return resp


def delete_edge(tm1_service: TM1Service, edge: Edge) -> Response:
    dimension, hierarchy = _edge_context_from_path(source_path=edge.source_path)
    logger.debug(f"Removing Edge: {edge.name} from Hierarchy: {hierarchy}.")
    return tm1_service.elements.remove_edge(hierarchy, dimension, edge.parent, edge.name)
