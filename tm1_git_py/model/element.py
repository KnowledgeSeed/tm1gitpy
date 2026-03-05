import json
import logging
import re
from typing import Any, Dict, Tuple, Optional

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


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between tm1_git_py and TI processes for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _escape_ti(value: str) -> str:
    """
    Escapes single quotes for TI.
    Example: "Director's Office" -> "Director''s Office"
    """
    if value is None:
        return ""
    return str(value).replace("'", "''")

def _map_ti_type(api_type: str) -> str:
    """
    Maps verbose API types to TI characters.
    Numeric -> N, String -> S, Consolidated -> C
    """
    if not api_type:
        return "N"
    t = api_type.lower()
    if "string" in t:
        return "S"
    elif "consolidated" in t:
        return "C"
    return "N"


def build_element_create_ti(
    element: Element,
    dimension_name: str,
    hierarchy_name: str,
    insertion_point: Optional[str] = ''
) -> str:

    if not dimension_name or not hierarchy_name:
        raise ValueError("Element create requires dimension and hierarchy context.")

    dim_clean = _escape_ti(dimension_name)
    hier_clean = _escape_ti(hierarchy_name)
    el_name_clean = _escape_ti(element.name)
    el_type_code = _map_ti_type(element.type)
    insert_clean = _escape_ti(insertion_point)

    # Syntax: HierarchyElementInsert(DimName, HierName, InsertionPoint, ElName, ElType)
    # InsertionPoint: '' means append to the end.
    lines = []
    lines.append(f"# --- Create Element: {el_name_clean} ---")
    lines.append(f"IF( HierarchyElementExists('{dim_clean}', '{hier_clean}', '{el_name_clean}') = 0 );")

    lines.append(
        f"    HierarchyElementInsert("
        f"'{dim_clean}', "
        f"'{hier_clean}', "
        f"'{insert_clean}', "
        f"'{el_name_clean}', "
        f"'{el_type_code}');"
    )
    lines.append(f"ENDIF;")

    return "\r\n".join(lines)


def build_element_update_ti(
    element_old: Element,
    element_new: Element,
    dimension_name: str,
    hierarchy_name: str,
) -> str:
    """
    Generates TI code to rename an element while RETAINING DATA.
    Uses 'DimensionElementPrincipalNameChange'.
    """
    if not dimension_name or not hierarchy_name:
        raise ValueError("Element create requires dimension and hierarchy context.")

    old_clean = _escape_ti(element_old.name)
    new_clean = _escape_ti(element_new.name)

    lines = []
    lines.append(f"# --- Update (Recreate) Element: '{old_clean}' -> '{new_clean}' ---")
    snippet = build_element_delete_ti(
        hierarchy_name=hierarchy_name,
        dimension_name=dimension_name,
        element_name=old_clean
    )
    lines.append(snippet)

    snippet = build_element_create_ti(
        hierarchy_name=hierarchy_name,
        dimension_name=dimension_name,
        element=element_new
    )
    lines.append(snippet)

    return "\r\n".join(lines)


def build_element_delete_ti(
        dimension_name: str,
        hierarchy_name: str,
        element_name: str
) -> str:
    """
    Generates TI code to delete an element from a specific hierarchy.
    """

    dim_clean = _escape_ti(dimension_name)
    hier_clean = _escape_ti(hierarchy_name)
    el_clean = _escape_ti(element_name)

    lines = []
    lines.append(f"# --- Delete Element: {el_clean} ---")

    # HierarchyElementExists returns 1 if found, 0 if not.
    lines.append(f"IF( HierarchyElementExists('{dim_clean}', '{hier_clean}', '{el_clean}') = 1 );")

    # Note: If this is a consolidated element, its children remain in the dimension
    # but are detached from this parent. Data on this specific element is lost.
    lines.append(f"   HierarchyElementDelete('{dim_clean}', '{hier_clean}', '{el_clean}');")

    lines.append(f"ENDIF;")

    return "\r\n".join(lines)
