import json
import logging
from typing import Any, Dict
from TM1py import TM1Service
from requests import Response

# {
#     "ParentName":"Provider Total",
#     "ComponentName":"ProviderTest",
#     "Weight":1
# }

class Edge:
    def __init__(self, parent, name, weight):
        self.parent = parent
        self.name = name
        self.weight = weight

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
    def from_dict(cls, data: Dict[str, Any]) -> "Edge":
        parent = data.get("parentName") or data.get("parent") or data.get("ParentName")
        component = (
            data.get("componentName") or data.get("name") or data.get("ComponentName")
        )
        weight = data.get("weight")
        if weight is None:
            weight = data.get("Weight")
        if weight is None:
            weight = 1
        return cls(parent=parent, name=component, weight=weight)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def create_edge(tm1_service: TM1Service, hierarchy: str, dimension: str, edge: Edge) -> Response:
    edge_name = {(edge.parent, edge.name), edge.weight}
    logger.debug(f"Creating Edge: {edge.name} in Hierarchy: {hierarchy}.")
    return tm1_service.elements.add_edges(hierarchy, dimension, edge_name)


def delete_edge(tm1_service: TM1Service, hierarchy: str, dimension: str, edge: Edge) -> Response:
    logger.debug(f"Removing Edge: {edge.name} from Hierarchy: {hierarchy}.")
    return tm1_service.elements.remove_edge(hierarchy, dimension, edge.parent, edge.name)
