import json
import logging
import re
from typing import Any, Dict, Tuple

import TM1py
from TM1py import TM1Service, Element
from requests import Response

# {
#   "Name": "CapexName",
#   "Type": "String"
# }


class Element:
    def __init__(self, name, type):
        self.name = name
        self.type = type

    def __init__(self, data: dict):
        for key, value in data.items():
            setattr(self, key.lower(), value)
            #for test
            if not hasattr(self, 'name'):
                self.name = None
            if not hasattr(self, 'type'):
               self.type = None

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
            'name': self.name,
            'type': self.type
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Element":
        return cls(
            name=data.get("name") or data.get("Name"),
            type=data.get("type") or data.get("Type")
        )


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def create_element(tm1_service: TM1Service, hierarchy_name: str, dimension_name: str, element: Element) -> Response:
    element_object = TM1py.Element(name=element.name, element_type=element.type)
    logger.debug(f"Creating Element: {element.name} in Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.create(hierarchy_name=hierarchy_name, dimension_name=dimension_name, element=element_object)


def update_element(tm1_service: TM1Service, hierarchy_name: str, dimension_name: str, element: Element) -> Response:
    element_object = tm1_service.elements.get(dimension_name=dimension_name, hierarchy_name=hierarchy_name, element_name=element.name)
    element_object.element_type = element.type
    logger.debug(f"Updating Element: {element.name} in Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.update(dimension_name=dimension_name, hierarchy_name=hierarchy_name, element=element_object)


def delete_element(tm1_service: TM1Service, hierarchy_name: str, dimension_name: str, element_name: str) -> Response:
    logger.debug(f"Deleting Element: {element_name} of Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.delete(hierarchy_name=hierarchy_name, dimension_name=dimension_name, element_name=element_name)
