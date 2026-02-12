import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import TM1py
from TM1py import TM1Service, Subset
from requests import Response


# {
# 	"@type":"Subset",
# 	"Name":"jhj",
# 	"Expression":"{[Balance Sheet Planning Ledger].[Balance Sheet Planning Ledger].Members}"
# }


class Subset:
    def __init__(self, name, expression, source_path: str):
        self.type = 'Subset'
        self.name = name
        self.expression = expression
        self.source_path = source_path

    def as_json(self):
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "Expression": self.expression
        }, indent='\t')

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Subset):
            return NotImplemented
        return self.name == other.name and \
               self.expression == other.expression

    def __hash__(self) -> int:
        return hash((self.name, self.expression))

    def to_dict(self):
        return {
            'name': self.name,
            'expression': self.expression
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        *,
        source_path: Optional[str] = None,
        dimension_name: Optional[str] = None,
        hierarchy_name: Optional[str] = None,
    ) -> "Subset":

        name = data.get("name") or data.get("Name")
        expression = data.get("expression") or data.get("Expression")
        resolved_path = source_path
        if resolved_path is None and dimension_name and hierarchy_name and name:
            resolved_path = f"dimensions/{dimension_name}.hierarchies/{hierarchy_name}.subsets/{name}.json"
        if resolved_path is None:
            raise ValueError(
                "Subset.from_dict requires either source_path or dimension/hierarchy context."
            )
        return cls(name=name, expression=expression, source_path=resolved_path)

    @staticmethod
    def as_link(dimension_name_base, hierarchy_name_base, name):
        # /dimensions/Dimension_A.hierarchies/Dimension_A.subsets/Subset_A.json
        return '/dimensions/' + dimension_name_base + '.hierarchies/' + hierarchy_name_base + '.subsets/' + name


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def _subset_context_from_path(source_path: str) -> Tuple[str, str]:
    dimension_name = re.search(r'/(\w*)(.hierarchies)', source_path).group(1)
    hierarchy_name = re.search(r'/(\w*)(.subsets)', source_path).group(1)
    return dimension_name, hierarchy_name


def create_subset(tm1_service: TM1Service, subset: Subset) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_path(subset.source_path)

    subset_object = TM1py.Subset(
        subset_name=subset.name,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
        expression=subset.expression,
    )
    logger.info(f"Creating Subset: {subset.name} in Hierarchy: {hierarchy_name}.")

    return tm1_service.subsets.create(subset_object)


def update_subset(tm1_service: TM1Service, subset: Dict[str, Any]) -> Response:
    subset_new = subset.get('new')
    dimension_name, hierarchy_name = _subset_context_from_path(subset_new.source_path)

    subset_object = tm1_service.subsets.get(subset_name=subset_new.name, dimension_name=dimension_name, hierarchy_name=hierarchy_name)
    subset_object.expression = subset_new.expression
    logger.info(f"Updating Subset: {subset_new.name} in Hierarchy: {hierarchy_name}.")

    return tm1_service.subsets.update(subset_object)


def delete_subset(tm1_service: TM1Service, subset: Subset) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_path(subset.source_path)

    logger.info(f"Deleting Subset: {subset.name} from Hierarchy: {hierarchy_name}.")
    return tm1_service.subsets.delete(subset_name=subset.name, dimension_name=dimension_name, hierarchy_name=hierarchy_name)

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


def build_subset_update_ti(subset: Dict[str, Any]) -> str:
    """
    Generates TI code to update a Subset's MDX expression.
    Expects the 'subset' dict to contain a 'new' key with the target Subset object.
    """

    # 1. Extract the Target Object
    # Based on your input structure: subset = {'new': SubsetObject, ...}
    subset_new = subset.get('new')

    if not subset_new:
        return "# Error: Missing 'new' state for subset update."

    # 2. Resolve Context
    dimension_name, hierarchy_name = _subset_context_from_path(subset_new.source_path)

    dim_name_clean = _escape_ti(dimension_name)
    hier_name_clean = _escape_ti(hierarchy_name)
    sub_name_clean = _escape_ti(subset.name)

    # Critical: MDX expressions often contain single quotes (e.g., [Dim].[Hier].[Elem]).
    # _escape_ti turns "'" into "''" ensuring the TI string doesn't break.
    mdx_clean = _escape_ti(subset_new.expression)

    lines = []
    lines.append(f"# --- Update Subset: {sub_name_clean} in {dim_name_clean} ---")

    # 5. Check Existence
    lines.append(f"IF( HierarchySubsetExists('{dim_name_clean}', '{hier_name_clean}', '{sub_name_clean}') = 1 );")

    # 6. Apply Expression
    # This updates the definition. If mdx_clean is empty, the subset becomes static.
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
