import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import TM1py
from TM1py import TM1Service, Subset
from requests import Response

from tm1_git_py.model.tm1git_json import dumps_tm1git


# {
# 	"@type":"Subset",
# 	"Name":"jhj",
# 	"Expression":"{[Balance Sheet Planning Ledger].[Balance Sheet Planning Ledger].Members}"
# }


def _element_reference_id_from_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        element_id = payload.get("@id") or payload.get("@odata.id")
        if isinstance(element_id, str):
            return element_id
    raise ValueError(f"Unable to resolve subset element reference id from payload: {payload!r}")


class Subset:
    def __init__(self, name, expression=None, element_ids: Optional[List[str]] = None):
        self.type = 'Subset'
        self.name = name
        self.expression = expression
        self.element_ids = list(element_ids or [])
        if self.is_dynamic and self.element_ids:
            raise ValueError("Dynamic subsets cannot carry static element reference ids.")

    @property
    def is_dynamic(self) -> bool:
        return self.expression not in (None, "")

    @property
    def is_static(self) -> bool:
        return not self.is_dynamic

    def as_json(self):
        return dumps_tm1git(self._json_payload())

    def _json_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "@type": self.type,
            "Name": self.name,
        }
        payload.update(self.to_dict(exclude_name=True))
        return payload

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Subset):
            return NotImplemented
        return self.name == other.name and \
               self.expression == other.expression and \
               self.element_ids == other.element_ids

    def __hash__(self) -> int:
        return hash((self.name, self.expression, tuple(self.element_ids)))

    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self, *, exclude_name: bool = False):
        payload: Dict[str, Any] = {} if exclude_name else {"Name": self.name}
        if self.is_dynamic:
            payload["Expression"] = self.expression
        else:
            payload["Elements"] = [{"@id": element_id} for element_id in self.element_ids]
        return payload

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any]
    ) -> "Subset":

        name = data.get("name") or data.get("Name")
        expression = data.get("expression")
        if expression is None:
            expression = data.get("Expression")
        element_payloads = []
        if expression in (None, ""):
            element_payloads = data.get("element_ids")
            if element_payloads is None:
                element_payloads = data.get("elements")
            if element_payloads is None:
                element_payloads = data.get("Elements") or []
        return cls(
            name=name,
            expression=expression,
            element_ids=[
                _element_reference_id_from_payload(payload)
                for payload in element_payloads
            ],
        )

    @staticmethod
    def uri_for(dimension_name: str, hierarchy_name: str, subset_name: str) -> str:
        return f"Dimensions('{dimension_name}')/Hierarchies('{hierarchy_name}')/Subsets('{subset_name}')"

    def uri(self, dimension_name: str, hierarchy_name: str) -> Optional[str]:
        if not dimension_name or not hierarchy_name or not self.name:
            return None
        return self.uri_for(dimension_name, hierarchy_name, self.name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_ELEMENT_REFERENCE_ID_PATTERN = re.compile(
    r"^Dimensions\('((?:''|[^'])*)'\)/"
    r"Hierarchies\('((?:''|[^'])*)'\)/"
    r"Elements\('((?:''|[^'])*)'\)$"
)

def _subset_context_from_uri(uri: str) -> Tuple[str, str]:
    match = re.search(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)/Subsets\('([^']+)'\)$", uri or "")
    if not match:
        raise ValueError(f"Invalid subset uri format: '{uri}'")
    dimension_name, hierarchy_name, _subset_name = match.groups()
    return dimension_name, hierarchy_name


def _unescape_reference_name(name: str) -> str:
    return name.replace("''", "'")


def _element_name_from_reference_id(
    element_id: str,
    dimension_name: str,
    hierarchy_name: str,
) -> str:
    match = _ELEMENT_REFERENCE_ID_PATTERN.match(element_id or "")
    if not match:
        raise ValueError(f"Invalid static subset element reference id: '{element_id}'")

    element_dimension_name, element_hierarchy_name, element_name = (
        _unescape_reference_name(name)
        for name in match.groups()
    )
    if element_dimension_name != dimension_name or element_hierarchy_name != hierarchy_name:
        raise ValueError(
            f"Static subset element reference id '{element_id}' does not belong to "
            f"dimension '{dimension_name}' and hierarchy '{hierarchy_name}'."
        )
    return element_name


def _static_subset_element_names(
    subset: Subset,
    dimension_name: str,
    hierarchy_name: str,
) -> List[str]:
    return [
        _element_name_from_reference_id(element_id, dimension_name, hierarchy_name)
        for element_id in subset.element_ids
    ]


def create_subset(tm1_service: TM1Service, subset: Subset, uri: Optional[str] = None) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_uri(uri)

    subset_kwargs = {
        "subset_name": subset.name,
        "dimension_name": dimension_name,
        "hierarchy_name": hierarchy_name,
    }
    if subset.is_dynamic:
        subset_kwargs["expression"] = subset.expression
    else:
        subset_kwargs["elements"] = _static_subset_element_names(
            subset,
            dimension_name,
            hierarchy_name,
        )

    subset_object = TM1py.Subset(**subset_kwargs)
    logger.info(f"Creating Subset: {subset.name} in Hierarchy: {hierarchy_name}.")

    return tm1_service.subsets.create(subset_object)


def update_subset(tm1_service: TM1Service, subset: Subset, uri: Optional[str] = None) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_uri(uri)

    subset_object = tm1_service.subsets.get(
        subset_name=subset.name,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name
    )

    if subset.is_static:
        static_element_names = _static_subset_element_names(
            subset,
            dimension_name,
            hierarchy_name,
        )
        if subset_object.is_dynamic:
            tm1_service.subsets.make_static(subset_name=subset.name, dimension_name=dimension_name, hierarchy_name=hierarchy_name)
        subset_object.expression = None
        subset_object.elements = static_element_names
    else:
        subset_object.expression = subset.expression
        subset_object.elements = []

    logger.info(f"Updating Subset: {subset.name} in Hierarchy: {hierarchy_name}.")
    return tm1_service.subsets.update(subset_object)


def delete_subset(tm1_service: TM1Service, subset: Subset, uri: Optional[str] = None) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_uri(uri)

    logger.info(f"Deleting Subset: {subset.name} from Hierarchy: {hierarchy_name}.")
    return tm1_service.subsets.delete(
        subset_name=subset.name,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name
    )

# ---------------------------------------------------------------------------------------------------
# Utility: interface between tm1_git_py and TI processes for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _escape_ti(value: str) -> str:
    return str(value).replace("'", "''") if value else ""


def build_subset_create_ti(subset: Subset) -> str:
    """
    Generates TI code to create a Subset.
    """

    # 1. Resolve Context
    # We assume _subset_context_from_path is available in your scope
    dimension_name, hierarchy_name = _subset_context_from_path(subset.source_path)

    # 3. Sanitize
    dim_name_clean = _escape_ti(dimension_name)
    hier_name_clean = _escape_ti(hierarchy_name)
    sub_name_clean = _escape_ti(subset.name)

    lines = []
    lines.append(f"# --- Create Subset: {sub_name_clean} in {hier_name_clean} ---")

    # 4. Create the Container (Idempotent)
    # HierarchySubsetExists(DimName, HierName, SubsetName) returns 1 if exists.
    lines.append(f"IF( HierarchySubsetExists('{dim_name_clean}', '{hier_name_clean}', '{sub_name_clean}') = 0 );")
    # HierarchySubsetCreate(DimName, HierName, SubName, [AsTemporary]); -> 0 for Permanent
    lines.append(f"    HierarchySubsetCreate('{dim_name_clean}', '{hier_name_clean}', '{sub_name_clean}', 0);")
    lines.append(f"ENDIF;")

    # 5. Apply MDX Expression (If Dynamic)
    # The snippet implies if 'expression' is present, we set it.
    if subset.expression:
        mdx_clean = _escape_ti(subset.expression)
        # HierarchySubsetMDXSet turns a static subset into a dynamic one or updates the MDX.
        lines.append(f"HierarchySubsetMDXSet('{dim_name_clean}', '{hier_name_clean}', '{sub_name_clean}', '{mdx_clean}');")

    return "\r\n".join(lines)


def build_subset_update_ti(subset: Subset) -> str:
    """
    Generates TI code to update a Subset's MDX expression.
    Expects the 'subset' dict to contain a 'new' key with the target Subset object.
    """

    dimension_name, hierarchy_name = _subset_context_from_path(subset.source_path)

    dim_name_clean = _escape_ti(dimension_name)
    hier_name_clean = _escape_ti(hierarchy_name)
    sub_name_clean = _escape_ti(subset.name)

    # Critical: MDX expressions often contain single quotes (e.g., [Dim].[Hier].[Elem]).
    # _escape_ti turns "'" into "''" ensuring the TI string doesn't break.
    mdx_clean = _escape_ti(subset.expression)

    lines = []
    lines.append(f"# --- Update Subset: {sub_name_clean} in {dim_name_clean} ---")
    lines.append(f"IF( HierarchySubsetExists('{dim_name_clean}', '{hier_name_clean}', '{sub_name_clean}') = 1 );")
    lines.append(f"    HierarchySubsetMDXSet('{dim_name_clean}', '{hier_name_clean}', '{sub_name_clean}', '{mdx_clean}');")
    lines.append(f"ENDIF;")

    return "\r\n".join(lines)


def build_subset_delete_ti(subset: Subset) -> str:
    """
    Generates TI code to delete a Subset.
    """

    # 1. Resolve Context
    dimension_name, hierarchy_name = _subset_context_from_path(subset.source_path)

    # 3. Sanitize
    dim_name_clean = _escape_ti(dimension_name)
    hier_name_clean = _escape_ti(hierarchy_name)
    sub_name_clean = _escape_ti(subset.name)

    lines = []
    lines.append(f"# --- Delete Subset: {sub_name_clean} from {dim_name_clean} ---")

    # 4. Check Existence
    # HierarchySubsetExists returns 1 if it exists.
    # Checking prevents errors if the subset was already deleted.
    lines.append(f"IF( HierarchySubsetExists('{dim_name_clean}', '{hier_name_clean}', '{sub_name_clean}') = 1 );")

    # 5. Delete
    lines.append(f"    HierarchySubsetDestroy('{dim_name_clean}', '{hier_name_clean}', '{sub_name_clean}');")

    lines.append(f"ENDIF;")

    return "\r\n".join(lines)
