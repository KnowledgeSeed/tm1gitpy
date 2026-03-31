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
    def __init__(self, name, expression):
        self.type = 'Subset'
        self.name = name
        self.expression = expression

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
        data: Dict[str, Any]
    ) -> "Subset":

        name = data.get("name") or data.get("Name")
        expression = data.get("expression") or data.get("Expression")
        return cls(
            name=name,
            expression=expression,
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

def _subset_context_from_path(source_path: str) -> Tuple[str, str]:
    dimension_name = re.search(r'/([\w}]*)(.hierarchies)', source_path).group(1)
    hierarchy_name = re.search(r'/([\w}]*)(.subsets)', source_path).group(1)
    return dimension_name, hierarchy_name


def create_subset(tm1_service: TM1Service, subset: Subset, source_path: Optional[str] = None) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_path(source_path)

    subset_object = TM1py.Subset(
        subset_name=subset.name,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
        expression=subset.expression,
    )
    logger.info(f"Creating Subset: {subset.name} in Hierarchy: {hierarchy_name}.")

    return tm1_service.subsets.create(subset_object)


def update_subset(tm1_service: TM1Service, subset: Subset, source_path: Optional[str] = None) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_path(source_path)

    subset_object = tm1_service.subsets.get(
        subset_name=subset.name,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name
    )
    subset_object.expression = subset.expression
    logger.info(f"Updating Subset: {subset.name} in Hierarchy: {hierarchy_name}.")

    return tm1_service.subsets.update(subset_object)


def delete_subset(tm1_service: TM1Service, subset: Subset, source_path: Optional[str] = None) -> Response:
    dimension_name, hierarchy_name = _subset_context_from_path(source_path)

    logger.info(f"Deleting Subset: {subset.name} from Hierarchy: {hierarchy_name}.")
    return tm1_service.subsets.delete(
        subset_name=subset.name,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name
    )
