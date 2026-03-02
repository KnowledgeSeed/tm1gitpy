import json
import logging
import re
from typing import Any, Dict, Optional

import TM1py
from TM1py import TM1Service
from requests import Response

# {
#   "Name": "CapexName",
#   "Type": "String"
# }


class Element:
    def __init__(
        self,
        name: str,
        type: str,
        *,
        source_path: Optional[str] = None,
        dimension_name: Optional[str] = None,
        hierarchy_name: Optional[str] = None
    ):
        self.name = name
        self.type = type
        self.source_path = source_path or self.as_link(dimension_name, hierarchy_name, name)
    """
    def __init__(self, data: dict):
        for key, value in data.items():
            setattr(self, key.lower(), value)
            #for test
            if not hasattr(self, 'name'):
                self.name = None
            if not hasattr(self, 'type'):
               self.type = None
    """
    def as_json(self):
        return json.dumps({
            "Name": self.name,
            "Type": self.type
        }, indent=4)
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Element):
            return NotImplemented
        return self.name == other.name and \
               self.type == other.type

    def __hash__(self) -> int:
        return hash((self.name, self.type))
    
    def to_dict(self):
        return {
            'name': self.name,
            'type': self.type
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *,
                  source_path: Optional[str] = None,
                  dimension_name: Optional[str] = None,
                  hierarchy_name: Optional[str] = None
    ) -> "Element":
        name = data.get("name") or data.get("Name") or None
        return cls(
            name=name,
            type=data.get("type") or data.get("Type") or None,
            source_path=source_path or cls.as_link(dimension_name, hierarchy_name, name)
        )

    @staticmethod
    def as_link(dimension_name_base: Optional[str], hierarchy_name_base: Optional[str], name: Optional[str]) -> Optional[str]:
        # dimensions/Dimension_A.hierarchies/Dimension_A.json/element1
        if dimension_name_base and hierarchy_name_base and name:
            return f"dimensions/{dimension_name_base}.hierarchies/{hierarchy_name_base}.json/{name}"
        return None

# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def _edge_context_from_path(source_path: str) -> tuple[str, str]:
    dimension_name = re.search(r'/([\w}]*)(.hierarchies)', source_path).group(1)
    hierarchy_name = re.search(r"/([^/]+)\.json$", source_path).group(1)
    return dimension_name, hierarchy_name


def create_element(tm1_service: TM1Service, element: Element) -> Response:
    dimension_name, hierarchy_name = _edge_context_from_path(source_path=element.source_path)
    element_object = TM1py.Element(name=element.name, element_type=element.type)
    logger.debug(f"Creating Element: {element.name} in Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.create(hierarchy_name=hierarchy_name, dimension_name=dimension_name, element=element_object)


def update_element(tm1_service: TM1Service, element: Element) -> Response:
    dimension_name, hierarchy_name = _edge_context_from_path(source_path=element.source_path)
    element_object = tm1_service.elements.get(dimension_name=dimension_name, hierarchy_name=hierarchy_name, element_name=element.name)
    element_object.element_type = element.type
    logger.debug(f"Updating Element: {element.name} in Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.update(dimension_name=dimension_name, hierarchy_name=hierarchy_name, element=element_object)


def delete_element(tm1_service: TM1Service, element: Element) -> Response:
    dimension_name, hierarchy_name = _edge_context_from_path(source_path=element.source_path)
    logger.debug(f"Deleting Element: {element.name} of Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.delete(hierarchy_name=hierarchy_name, dimension_name=dimension_name, element_name=element.name)
