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
        component_name,
        weight
    ):
        self.parent = parent
        self.component_name = component_name
        self.weight = weight

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Edge):
            return NotImplemented
        return self.parent == other.parent and \
               self.component_name == other.component_name and \
               self.weight == other.weight

    def __hash__(self) -> int:
        return hash((self.parent, self.component_name, self.weight))

    @property
    def name(self) -> str:
        """Alias for component_name for compatibility with generic code (e.g. obj.name)."""
        return self.component_name

    def __repr__(self):
        return f"Edge('{self.parent}/{self.component_name}')"

    def to_dict(self):
        return {
            'parentName': self.parent,
            'componentName': self.component_name,
            'weight': self.weight
        }

    def as_json(self):
        return json.dumps({
            "ParentName": self.parent,
            "ComponentName": self.component_name,
            "Weight": self.weight
        }, indent=4)

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any]
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
            component_name=component,
            weight=weight,
        )

    @staticmethod
    def uri_for(
        dimension_name: Optional[str],
        hierarchy_name: Optional[str],
        parent: Optional[str],
        component: Optional[str],
    ) -> Optional[str]:
        if dimension_name and hierarchy_name and parent and component:
            return (
                f"Dimensions('{dimension_name}')/Hierarchies('{hierarchy_name}')/"
                f"Edges('{parent}/{component}')"
            )
        return None

    def uri(self, dimension_name: str, hierarchy_name: str) -> Optional[str]:
        if not dimension_name or not hierarchy_name or not self.parent or not self.component_name:
            return None
        return self.uri_for(dimension_name, hierarchy_name, self.parent, self.component_name)


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


def create_edge(tm1_service: TM1Service, edge: Edge, source_path: Optional[str] = None) -> Response:
    dimension, hierarchy = _edge_context_from_path(source_path=source_path)
    edge_name = {(edge.parent, edge.component_name): edge.weight}
    logger.debug(f"Creating Edge: {edge.component_name} in Hierarchy: {hierarchy}.")
    return tm1_service.elements.add_edges(hierarchy, dimension, edge_name)


def update_edge(tm1_service: TM1Service, edge: Edge, source_path: Optional[str] = None) -> Response:
    dimension, hierarchy = _edge_context_from_path(source_path=source_path)
    hierarchy_object = tm1_service.hierarchies.get(dimension_name=dimension, hierarchy_name=hierarchy)
    hierarchy_object.update_edge(parent=edge.parent, component=edge.component_name, weight=edge.weight)
    resp = tm1_service.hierarchies.update(hierarchy_object)
    if isinstance(resp, list):
        resp = resp[0]
    return resp


def delete_edge(tm1_service: TM1Service, edge: Edge, source_path: Optional[str] = None) -> Response:
    dimension, hierarchy = _edge_context_from_path(source_path=source_path)
    logger.debug(f"Removing Edge: {edge.component_name} from Hierarchy: {hierarchy}.")
    return tm1_service.elements.remove_edge(hierarchy, dimension, edge.parent, edge.component_name)
