import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import TM1py
from TM1py import TM1Service, Subset
from requests import Response


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
        return json.dumps(self._json_payload(), indent='\t')

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
