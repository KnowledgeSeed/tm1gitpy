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
        weight,
        *,
        component_index: Optional[int] = None,
    ):
        self.parent = parent
        self.component_name = component_name
        self.weight = weight
        self.component_index = component_index

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
            "ParentName": self.parent,
            "ComponentName": self.component_name,
            "Weight": self.weight,
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
        component_index = data.get("component_index")
        if component_index is None:
            component_index = data.get("ComponentIndex")
        return cls(
            parent=parent,
            component_name=component,
            weight=weight,
            component_index=component_index,
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
                f"Edges('{parent}'/'{component}')"
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

def _edge_context_from_uri(uri: str) -> tuple[str, str]:
    match = re.search(
        r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)/Edges\((?:'[^']+'/'[^']+'|'[^']+')\)$",
        uri or "",
    )
    if not match:
        raise ValueError(f"Invalid edge uri format: '{uri}'")
    dimension_name, hierarchy_name = match.groups()
    return dimension_name, hierarchy_name


def create_edge(tm1_service: TM1Service, edge: Edge, uri: Optional[str] = None) -> Response:
    dimension, hierarchy = _edge_context_from_uri(uri=uri)
    edge_name = {(edge.parent, edge.component_name): edge.weight}
    logger.debug(f"Creating Edge: {edge.component_name} in Hierarchy: {hierarchy}.")
    return tm1_service.elements.add_edges(hierarchy, dimension, edge_name)


def update_edge(tm1_service: TM1Service, edge: Edge, uri: Optional[str] = None) -> Response:
    dimension, hierarchy = _edge_context_from_uri(uri=uri)
    hierarchy_object = tm1_service.hierarchies.get(dimension_name=dimension, hierarchy_name=hierarchy)
    hierarchy_object.update_edge(parent=edge.parent, component=edge.component_name, weight=edge.weight)
    resp = tm1_service.hierarchies.update(hierarchy_object)
    if isinstance(resp, list):
        resp = resp[0]
    return resp


def delete_edge(tm1_service: TM1Service, edge: Edge, uri: Optional[str] = None) -> Response:
    dimension, hierarchy = _edge_context_from_uri(uri=uri)
    logger.debug(f"Removing Edge: {edge.component_name} from Hierarchy: {hierarchy}.")
    return tm1_service.elements.remove_edge(hierarchy, dimension, edge.parent, edge.component_name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between tm1_git_py and TI processes for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _escape_ti(value: str) -> str:
    return str(value).replace("'", "''") if value else ""


def build_edge_create_ti(edge: Edge) -> str:
    """
    Generates TI to add a component (child) to a parent.
    Acts as an Upsert (Updates weight if edge already exists).
    """
    dimension_name, hierarchy_name = _edge_context_from_path(source_path=edge.source_path)

    # 1. Sanitize Inputs
    dim_clean = _escape_ti(dimension_name)
    hier_clean = _escape_ti(hierarchy_name)
    parent_clean = _escape_ti(edge.parent)
    child_clean = _escape_ti(edge.name)  # Assuming edge.name is the Child element
    weight = edge.weight

    lines = []
    lines.append(f"# --- Create Edge: {parent_clean} -> {child_clean} (Weight: {weight}) ---")
    lines.append(f"IF( ElementIsComponent('{dim_clean}', '{hier_clean}', '{child_clean}', '{parent_clean}') = 0 );")

    # 2. Add Component
    # Syntax: HierarchyElementComponentAdd(DimName, HierName, ConsolidatedElName, ElName, ElWeight);
    lines.append(
        f"    HierarchyElementComponentAdd('{dim_clean}', '{hier_clean}', '{parent_clean}', '{child_clean}', {weight});"
    )
    lines.append(f"ENDIF;")

    return "\r\n".join(lines)


def build_edge_update_ti(edge: Edge) -> str:
    """
    Interface for Component Upsert via the build_edge_create_ti function.
    """
    parent_clean = _escape_ti(edge.parent)
    child_clean = _escape_ti(edge.name)

    lines = []
    lines.append(f"# --- Update (Recreate) Edge: {parent_clean} -> {child_clean} ---")
    lines.append(build_edge_delete_ti(edge))
    lines.append(build_edge_create_ti(edge))
    return "\r\n".join(lines)


def build_edge_delete_ti(edge: Edge) -> str:
    """
    Generates TI to remove a component (child) from a parent.
    """
    dimension_name, hierarchy_name = _edge_context_from_path(source_path=edge.source_path)

    # 1. Sanitize Inputs
    dim_clean = _escape_ti(dimension_name)
    hier_clean = _escape_ti(hierarchy_name)
    parent_clean = _escape_ti(edge.parent)
    child_clean = _escape_ti(edge.name)

    lines = []
    lines.append(f"# --- Remove Edge: {parent_clean} -> {child_clean} ---")

    # 2. Check Existence
    # ElementIsComponent(Dim, Hier, Child, Parent ) returns 1 if true.
    lines.append(f"IF( ElementIsComponent('{dim_clean}', '{hier_clean}', '{child_clean}', '{parent_clean}') = 1 );")

    # 3. Delete Component
    # Syntax: HierarchyElementComponentDelete(DimName, HierName, ConsolidatedElName, ElName);
    lines.append(
        f"    HierarchyElementComponentDelete('{dim_clean}', '{hier_clean}', '{parent_clean}', '{child_clean}');"
    )

    lines.append(f"ENDIF;")

    return "\r\n".join(lines)
