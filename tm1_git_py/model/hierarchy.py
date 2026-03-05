import json
import logging
import re
from typing import List, Any, Dict, Optional, Tuple
import TM1py
from TM1py import TM1Service
from TM1py.Utils import format_url
from requests import Response

from .element import Element, create_element, delete_element, update_element, build_element_create_ti, \
    build_element_update_ti, build_element_delete_ti
from .edge import Edge, build_edge_create_ti
from .subset import Subset, build_subset_create_ti


# {
# 	"@type": "Hierarchy",
# 	"Name": "Capex Balance Sheet Assignment Measure",
# 	"Elements": [
# 		{
# 			"Name": "Assignment",
# 			"Type": "Numeric"
# 		},
# 		{
# 			"Name": "Comment",
# 			"Type": "String"
# 		},
# 		{
# 			"Name": "CapexName",
# 			"Type": "String"
# 		},
# 		{
# 			"Name": "BalanceSheetName",
# 			"Type": "String"
# 		},
# 		{
# 			"Name": "Value",
# 			"Type": "Numeric"
# 		}
# 	],
# 	"Subsets@Code.links": []
# }


class Hierarchy:
    def __init__(self, name, elements: List[Element], edges: List[Edge], subsets: List[Subset], source_path: str):
        self.type = 'Hierarchy'
        self.name = name
        self.elements = elements
        self.edges = edges
        self.subsets = subsets
        self.source_path = source_path

    def as_json(self):
        elements = [obj.to_dict() for obj in self.elements]
        edges = [obj.to_dict() for obj in self.edges]
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "Elements": elements,
            "Edges": edges,
            "Subsets@Code.links": [format_url("{}.subsets/{}.json", self.name, s.name) for s in self.subsets]
        }, indent='\t')

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Hierarchy):
            return NotImplemented
        
        if self.name != other.name:
            return False
        
        if set(self.elements) != set(other.elements):
            return False

        if set(self.edges) != set(other.edges):
            return False
        
        if set(self.subsets) != set(other.subsets):
            return False

        return True

    def __hash__(self) -> int:
        return hash((
            self.name,
            frozenset(self.elements),
            frozenset(self.edges),
            frozenset(self.subsets)
        ))
    
    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self):
        return {
            'name': self.name,
            'elements': [e.to_dict() for e in self.elements],
            'edges': [e.to_dict() for e in self.edges],
            'subsets': [s.to_dict() for s in self.subsets]
        }

    def asLink(self, dimension_name):
        # /dimensions/Dimension_A.hierarchies/Dimension_A.json
        return '/dimensions/' + dimension_name + '.hierarchies/' + self.name + '.json'

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any],
            *,
            source_path: Optional[str] = None,
            dimension_name: Optional[str] = None
    ) -> "Hierarchy":

        name = data.get("name") or data.get("Name")
        resolved_path = source_path
        if resolved_path is None and dimension_name and name:
            resolved_path = f"dimensions/{dimension_name}.hierarchies/{name}.json"
        if resolved_path is None:
            raise ValueError("Hierarchy.from_dict requires a source_path or dimension context.")

        element_payloads = data.get("elements") or data.get("Elements") or []
        edge_payloads = data.get("edges") or data.get("Edges") or []
        subset_payloads = data.get("subsets") or data.get("Subsets") or []
        subset_base_path = resolved_path.rsplit(".json", 1)[0] + ".subsets" if resolved_path else None

        elements: List[Element] = []
        for payload in element_payloads:
            element_name = payload.get("name") or payload.get("Name")
            element_path = f"{resolved_path}/{element_name}" if element_name else None
            elements.append(Element.from_dict(payload, source_path=element_path))

        edges: List[Edge] = []
        for payload in edge_payloads:
            parent = payload.get("parentName") or payload.get("parent") or payload.get("ParentName")
            component = payload.get("componentName") or payload.get("name") or payload.get("ComponentName")
            edge_path = f"{resolved_path}/{parent}:{component}" if parent and component else None
            edges.append(Edge.from_dict(payload, source_path=edge_path))
        subsets: List[Subset] = []
        for payload in subset_payloads:
            subset_name = payload.get("name") or payload.get("Name")
            subset_path = None
            if subset_base_path and subset_name:
                subset_path = f"{subset_base_path}/{subset_name}.json"
            subsets.append(
                Subset.from_dict(payload, source_path=subset_path, dimension_name=dimension_name, hierarchy_name=name)
            )

        return cls(name=name, elements=elements, edges=edges, subsets=subsets, source_path=resolved_path)

    @staticmethod
    def as_link(dimension_name_base, name):
        # /dimensions/Dimension_A.json
        return '/dimensions/' + dimension_name_base + '.hierarchies/' + name


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def _hierarchy_context_from_path(source_path: str) -> Tuple[str, str]:
    dimension_name = re.search(r'/([\w}]*)(.hierarchies)', source_path).group(1)
    hierarchy_name = re.search(r"/([^/]+)\.json$", source_path).group(1)
    return dimension_name, hierarchy_name


def create_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy) -> Response:
    dimension_name, _ = _hierarchy_context_from_path(hierarchy.source_path)
    hierarchy_object = TM1py.Hierarchy(name=hierarchy.name, dimension_name=dimension_name)
    response = tm1_service.hierarchies.create(hierarchy_object)
    logger.info(f"Created Hierarchy: {hierarchy.name}.")

    return response


def update_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy) -> Response:
    dimension_name, _ = _hierarchy_context_from_path(hierarchy.source_path)
    logger.info("Skipping direct Hierarchy update for '%s'; updates are handled by child changes.", hierarchy.name)
    return _build_noop_update_response(
        resource_url=format_url("/api/v1/Dimensions('{}')/Hierarchies('{}')", dimension_name, hierarchy.name),
        message=f"No-op Hierarchy update for '{hierarchy.name}'."
    )


def delete_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy) -> Response:
    dimension_name, _ = _hierarchy_context_from_path(hierarchy.source_path)
    logger.info(f"Deleting Hierarchy: {hierarchy.name} of Dimension: {dimension_name}.")
    return tm1_service.hierarchies.delete(dimension_name=dimension_name, hierarchy_name=hierarchy.name)


def _build_noop_update_response(resource_url: str, message: str) -> Response:
    response = Response()
    response.status_code = 200
    response.url = resource_url
    response._content = message.encode("utf-8")
    response.encoding = "utf-8"
    return response


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between tm1_git_py and TI processes for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _escape_ti(value: str) -> str:
    if value is None: return ""
    return str(value).replace("'", "''")

def _map_ti_type(api_type: str) -> str:
    if not api_type: return "N"
    t = api_type.lower()
    if "string" in t: return "S"
    if "consolidated" in t: return "C"
    return "N"


def build_hierarchy_create_ti(hierarchy: Hierarchy, dimension_name: Optional[str] = None) -> str:
    """
    Generates TI code to create a Hierarchy, Elements, and Edges.
    Does NOT check for Dimension existence (relies on TM1 erroring out to trigger rollback).
    """

    # 1. Resolve Context
    # Using your existing helper logic
    if not dimension_name:
        dimension_name, _ = _hierarchy_context_from_path(hierarchy.source_path)
    hierarchy_name = hierarchy.name

    # 2. Sanitize Inputs for TI
    dim_clean = _escape_ti(dimension_name)
    hier_clean = _escape_ti(hierarchy_name)

    lines = []
    lines.append(f"# --- Create Hierarchy: {dim_clean}:{hier_clean} ---")

    # 3. Create the Hierarchy Object
    # If the dimension is missing, 'HierarchyCreate' or 'HierarchyElementInsert'
    # will throw a critical error, ensuring the transaction rolls back.

    if dimension_name != hierarchy_name:
        # Only needed for named hierarchies.
        # The 'Leaves' hierarchy (same name as dim) is created automatically with the Dimension.

        lines.append(f"IF( HierarchyExists('{dim_clean}', '{hier_clean}') = 1 );")
        lines.append(f"   HierarchyCreate('{dim_clean}', '{hier_clean}');")
        lines.append(f"ENDIF;")

    # 4. Create Elements
    for element in hierarchy.elements:
        element_ti = build_element_create_ti(
            element=element,
            dimension_name=dimension_name,
            hierarchy_name=hierarchy_name
        )
        lines.append(element_ti)

    # 5. Create Edges (Parent-Child relationships)
    for edge in hierarchy.edges:
        edge_ti = build_edge_create_ti(
            dimension_name=dimension_name,
            hierarchy_name=hierarchy_name,
            edge=edge
        )
        lines.append(edge_ti)

    return "\r\n".join(lines)


def build_hierarchy_update_ti(
        hierarchy: Dict[str, Any]
) -> str:
    """
    Generates a TI script to synchronize elements between two hierarchy states.
    Handles Removes, Updates (Type changes), and Creates.
    """

    hierarchy_new = hierarchy.get("new")
    hierarchy_old = hierarchy.get("old")

    dimension_name, _ = _hierarchy_context_from_path(hierarchy_new.source_path)
    hierarchy_name = hierarchy_new.name

    elements_new = hierarchy_new.elements
    elements_old = hierarchy_old.elements
    # 1. Map Names to Objects for easy lookup
    old_map: Dict[str, Element] = {e.name: e for e in elements_old}
    new_map: Dict[str, Element] = {e.name: e for e in elements_new}

    old_names = set(old_map.keys())
    new_names = set(new_map.keys())

    # 2. Determine Actions based on Names
    names_to_remove = old_names - new_names
    names_to_create = new_names - old_names
    names_to_update = old_names.intersection(new_names)

    lines = []
    lines.append(f"# --- Synchronizing Elements for {dimension_name}:{hierarchy_name} ---")

    # 3. Handle REMOVALS
    if names_to_remove:
        lines.append(f"# -- removing {len(names_to_remove)} elements --")
        for name in names_to_remove:
            snippet = build_element_delete_ti(
                hierarchy_name=hierarchy_name,
                dimension_name=dimension_name,
                element_name=name
            )
            lines.append(snippet)

    # 4. Handle UPDATES
    # We pass the NEW element definition (target state) to the builder.
    # If an element has a type change, it gets recreated.
    # Type changes cause data loss!
    if names_to_update:
        lines.append(f"# -- checking updates for {len(names_to_update)} elements --")
        for name in names_to_update:
            target_element = new_map[name]
            source_element = old_map[name]
            if target_element.type != source_element.type:
                snippet = build_element_update_ti(
                    hierarchy_name=hierarchy_name,
                    dimension_name=dimension_name,
                    element_old=source_element,
                    element_new=target_element
                )
                lines.append(snippet)

    # 5. Handle CREATIONS
    if names_to_create:
        lines.append(f"# -- creating {len(names_to_create)} elements (Reverse processing) --")
        for i in range(len(elements_new) - 1, -1, -1):
            element = elements_new[i]

            if element.name in names_to_create:
                if i == len(elements_new) - 1:
                    insertion_point = ''
                else:
                    insertion_point = elements_new[i + 1].name

                snippet = build_element_create_ti(
                    hierarchy_name=hierarchy_name,
                    dimension_name=dimension_name,
                    element=element,
                    insertion_point=insertion_point
                )
                lines.append(snippet)

    return "\r\n".join(lines)


def build_hierarchy_delete_ti(
        hierarchy: Hierarchy,
) -> str:
    """
    Generates TI code to delete a hierarchy from a specific dimension.
    """
    dimension_name, _ = _hierarchy_context_from_path(hierarchy.source_path)

    # 1. Sanitize Inputs
    dim_clean = _escape_ti(dimension_name)
    hier_clean = _escape_ti(hierarchy.name)

    lines = []
    lines.append(f"# --- Delete Hierarchy: {hier_clean} ---")

    # 2. Check Existence
    # HierarchyExists returns 1 if found, 0 if not.
    lines.append(f"IF( HierarchyExists('{dim_clean}', '{hier_clean}') = 1 );")

    # 3. Delete
    lines.append(f"   HierarchyDestroy('{dim_clean}', '{hier_clean}');")

    lines.append(f"ENDIF;")

    return "\r\n".join(lines)
