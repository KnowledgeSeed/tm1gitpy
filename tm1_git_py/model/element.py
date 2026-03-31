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
        type: str
    ):
        self.name = name
        self.type = type
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
    
    def __repr__(self):
        return f"{self.type}('{self.name}')"
    
    def to_dict(self):
        return {
            "Name": self.name,
            "Type": self.type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Element":
        name = data.get("name") or data.get("Name") or None
        return cls(
            name=name,
            type=data.get("type") or data.get("Type") or None,
        )

    @staticmethod
    def uri_for(dimension_name: Optional[str], hierarchy_name: Optional[str], element_name: Optional[str]) -> Optional[str]:
        if dimension_name and hierarchy_name and element_name:
            return f"Dimensions('{dimension_name}')/Hierarchies('{hierarchy_name}')/Elements('{element_name}')"
        return None

    def uri(self, dimension_name: str, hierarchy_name: str) -> Optional[str]:
        if not dimension_name or not hierarchy_name or not self.name:
            return None
        return self.uri_for(dimension_name, hierarchy_name, self.name)

# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def _edge_context_from_path(source_path: str) -> tuple[str, str]:
    normalized_path = (source_path or "").replace("\\", "/").lstrip("/")
    match = re.search(r"dimensions/([^/]+)\.hierarchies/([^/]+)\.json(?:/|$)", normalized_path)
    if not match:
        raise ValueError(f"Invalid element source_path format: '{source_path}'")
    dimension_name, hierarchy_name = match.groups()
    return dimension_name, hierarchy_name


def create_element(tm1_service: TM1Service, element: Element, source_path: Optional[str] = None) -> Response:
    dimension_name, hierarchy_name = _edge_context_from_path(source_path=source_path)
    element_object = TM1py.Element(name=element.name, element_type=element.type)
    logger.debug(f"Creating Element: {element.name} in Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.create(hierarchy_name=hierarchy_name, dimension_name=dimension_name, element=element_object)


def update_element(tm1_service: TM1Service, element: Element, source_path: Optional[str] = None) -> Response:
    dimension_name, hierarchy_name = _edge_context_from_path(source_path=source_path)
    element_object = tm1_service.elements.get(dimension_name=dimension_name, hierarchy_name=hierarchy_name, element_name=element.name)
    element_object.element_type = element.type
    logger.debug(f"Updating Element: {element.name} in Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.update(dimension_name=dimension_name, hierarchy_name=hierarchy_name, element=element_object)


def delete_element(tm1_service: TM1Service, element: Element, source_path: Optional[str] = None) -> Response:
    dimension_name, hierarchy_name = _edge_context_from_path(source_path=source_path)
    logger.debug(f"Deleting Element: {element.name} of Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.delete(hierarchy_name=hierarchy_name, dimension_name=dimension_name, element_name=element.name)
