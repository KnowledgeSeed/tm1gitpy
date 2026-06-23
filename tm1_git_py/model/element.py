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
        element_index: Optional[int] = None,
    ):
        self.name = name
        self.type = type
        self.element_index = element_index

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
        return json.dumps({"Name": self.name, "Type": self.type}, indent=4)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Element):
            return NotImplemented
        return self.name == other.name and self.type == other.type

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
        element_index = data.get("element_index")
        if element_index is None:
            element_index = data.get("ElementIndex")
        return cls(
            name=name,
            type=data.get("type") or data.get("Type") or None,
            element_index=element_index,
        )

    @staticmethod
    def uri_for(
        dimension_name: Optional[str],
        hierarchy_name: Optional[str],
        element_name: Optional[str],
    ) -> Optional[str]:
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


def _element_context_from_uri(uri: str) -> tuple[str, str]:
    match = re.search(
        r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)/Elements\('([^']+)'\)$",
        uri or "",
    )
    if not match:
        raise ValueError(f"Invalid element uri format: '{uri}'")
    dimension_name, hierarchy_name, _element_name = match.groups()
    return dimension_name, hierarchy_name


def create_element(
    tm1_service: TM1Service, element: Element, uri: Optional[str] = None
) -> Response:
    dimension_name, hierarchy_name = _element_context_from_uri(uri=uri)
    element_object = TM1py.Element(name=element.name, element_type=element.type)
    logger.debug(f"Creating Element: {element.name} in Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.create(
        hierarchy_name=hierarchy_name,
        dimension_name=dimension_name,
        element=element_object,
    )


def update_element(
    tm1_service: TM1Service, element: Element, uri: Optional[str] = None
) -> Response:
    dimension_name, hierarchy_name = _element_context_from_uri(uri=uri)
    element_object = tm1_service.elements.get(
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
        element_name=element.name,
    )
    element_object.element_type = element.type
    logger.debug(f"Updating Element: {element.name} in Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.update(
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
        element=element_object,
    )


def delete_element(
    tm1_service: TM1Service, element: Element, uri: Optional[str] = None
) -> Response:
    dimension_name, hierarchy_name = _element_context_from_uri(uri=uri)
    logger.debug(f"Deleting Element: {element.name} of Hierarchy: {hierarchy_name}.")
    return tm1_service.elements.delete(hierarchy_name=hierarchy_name, dimension_name=dimension_name, element_name=element.name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between tm1_git_py and TI processes for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _escape_ti(value: str | None) -> str:
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
    uri: Optional[str] = None,
    insertion_point: Optional[str] = ''
) -> str:
    """
    Generates TI code to create an element.
    InsertionPoint: If given, the new Element is inserted before this existing Element.
        Default '' means append to the end.
    """

    dimension_name, hierarchy_name = _element_context_from_uri(uri=uri)

    dim_clean = _escape_ti(dimension_name)
    hier_clean = _escape_ti(hierarchy_name)
    el_name_clean = _escape_ti(element.name)
    el_type_code = _map_ti_type(element.type)
    insert_clean = _escape_ti(insertion_point)


    lines = [
        f"# --- Create Element: {el_name_clean} ---",
        f"IF( HierarchyElementExists('{dim_clean}', '{hier_clean}', '{el_name_clean}') = 0 );",
        f"    HierarchyElementInsert("
        f"'{dim_clean}', "
        f"'{hier_clean}', "
        f"'{insert_clean}', "
        f"'{el_name_clean}', "
        f"'{el_type_code}');",
        "ENDIF;"
    ]

    return "\r\n".join(lines)


def build_element_update_ti(element: Element, uri: Optional[str] = None,) -> str:
    """
    Generates TI code to rename an element.
    This function deletes and then recreates the Element object.
    """
    element_clean = _escape_ti(element.name)

    lines = [
        f"# --- Update (Recreate) Element: '{element_clean}' ---",
        build_element_delete_ti(element=element, uri=uri),
        build_element_create_ti(element=element, uri=uri)
    ]

    return "\r\n".join(lines)


def build_element_delete_ti(element: Element, uri: Optional[str] = None) -> str:
    """
    Generates TI code to delete an element from a specific hierarchy.
    Note: If this is a consolidated element, its children remain in the dimension
        but are detached from this parent. Data on this specific element is lost.
    """
    dimension_name, hierarchy_name = _element_context_from_uri(uri=uri)

    dim_clean = _escape_ti(dimension_name)
    hier_clean = _escape_ti(hierarchy_name)
    el_clean = _escape_ti(element.name)

    lines = [
        f"# --- Delete Element: {el_clean} ---",
        f"IF( HierarchyElementExists('{dim_clean}', '{hier_clean}', '{el_clean}') = 1 );",
        f"   HierarchyElementDelete('{dim_clean}', '{hier_clean}', '{el_clean}');",
        "ENDIF;"
    ]

    return "\r\n".join(lines)
