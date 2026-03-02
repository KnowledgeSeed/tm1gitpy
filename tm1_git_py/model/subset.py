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

    def __repr__(self):
        return f"{self.type}('{self.name}')"

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
    dimension_name = re.search(r'/([\w}]*)(.hierarchies)', source_path).group(1)
    hierarchy_name = re.search(r'/([\w}]*)(.subsets)', source_path).group(1)
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


def update_subset(tm1_service: TM1Service, subset: Subset) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_path(subset.source_path)

    subset_object = tm1_service.subsets.get(
        subset_name=subset.name,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name
    )
    subset_object.expression = subset.expression
    logger.info(f"Updating Subset: {subset.name} in Hierarchy: {hierarchy_name}.")

    return tm1_service.subsets.update(subset_object)


def delete_subset(tm1_service: TM1Service, subset: Subset) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_path(subset.source_path)

    logger.info(f"Deleting Subset: {subset.name} from Hierarchy: {hierarchy_name}.")
    return tm1_service.subsets.delete(
        subset_name=subset.name,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name
    )
