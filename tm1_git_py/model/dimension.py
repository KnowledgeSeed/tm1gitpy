import io
import logging
import re
from typing import List, Any, Dict, Union, Optional

import TM1py
from TM1py import TM1Service
from TM1py.Utils import format_url
from requests import Response

from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.tm1git_json import dump_as_tm1git

# Keys (at any object depth) that use ``"key" : value``; dimension export uses compact colons throughout.
DIMENSION_JSON_SPACED_COLON_KEYS: frozenset[str] = frozenset()


# {
# 	"@type":"Dimension",
# 	"Name":"Taxes Measure",
# 	"Hierarchies@Code.links":
# 	[
# 		"Taxes Measure.hierarchies/Taxes Measure.json"
# 	],
# 	"DefaultHierarchy":
# 	{
# 		"@id":"Dimensions('Taxes Measure')/Hierarchies('Taxes Measure')"
# 	}
# }

class Dimension:
    def __init__(
        self,
        name,
        hierarchies: Optional[List[Hierarchy]] = None,
        defaultHierarchy: Optional[Hierarchy] = None,
    ):
        self.type = 'Dimension'
        self.name = name
        self.hierarchies = list(hierarchies or [])
        self.defaultHierarchy = self._select_default_hierarchy(
            dimension_name=name,
            hierarchies=self.hierarchies,
            default_hierarchy=defaultHierarchy,
        )

    @staticmethod
    def _select_default_hierarchy(
        *,
        dimension_name: str,
        hierarchies: List[Hierarchy],
        default_hierarchy: Optional[Hierarchy],
    ) -> Hierarchy:
        matching_hierarchy = next((hier for hier in hierarchies if hier.name == dimension_name), None)
        if matching_hierarchy is not None:
            return matching_hierarchy
        if default_hierarchy is not None:
            return default_hierarchy
        if hierarchies:
            return hierarchies[0]
        return Hierarchy(name=dimension_name, elements=[], edges=[], subsets=[])

    def as_json(self):
        payload: Dict[str, Any] = {
            "@type": self.type,
            "Name": self.name,
            "Hierarchies@Code.links": [
                format_url("{}.hierarchies/{}.json", self.name, h.name)
                for h in self.hierarchies
            ],
            "DefaultHierarchy": {
                "@id": format_url(
                    "Dimensions('{}')/Hierarchies('{}')",
                    self.name,
                    self.defaultHierarchy.name,
                ),
            },
        }
        buf = io.StringIO()
        dump_as_tm1git(payload, buf, spaced_colon_keys=DIMENSION_JSON_SPACED_COLON_KEYS)
        return buf.getvalue()

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Dimension):
            return NotImplemented

        if self.name != other.name:
            return False

        if self.defaultHierarchy.name != other.defaultHierarchy.name:
            return False

        if set(self.hierarchies) != set(other.hierarchies):
            return False

        return True

    def __hash__(self) -> int:
        return hash((
            self.name,
            self.defaultHierarchy.name,
            frozenset(self.hierarchies)
        ))

    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self):
        return {
            'name': self.name,
            'hierarchies': [h.to_dict() for h in self.hierarchies],
            'defaultHierarchy': self.defaultHierarchy.to_dict()
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any]
    ) -> "Dimension":

        name = data.get("name") or data.get("Name")
        hierarchy_payloads = data.get("hierarchies") or data.get("Hierarchies") or []
        hierarchies = [Hierarchy.from_dict(payload) for payload in hierarchy_payloads]

        default_payload = data.get("defaultHierarchy") or data.get("DefaultHierarchy") or {}
        default_name = None
        if isinstance(default_payload, str):
            pattern = r"Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)"
            match = re.search(pattern, default_payload)
            if match:
                _, default_name = match.groups()
        elif isinstance(default_payload, dict):
            default_name = default_payload.get("name") or default_payload.get("Name")
            if not default_name:
                default_id = default_payload.get("@id") or default_payload.get("id")
                if isinstance(default_id, str):
                    pattern = r"Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)"
                    match = re.search(pattern, default_id)
                    if match:
                        _, default_name = match.groups()

        default_hierarchy = None
        if default_name:
            default_hierarchy = next((hier for hier in hierarchies if hier.name == default_name), None)
        if default_hierarchy is None and isinstance(default_payload, dict) and default_payload:
            default_hierarchy = Hierarchy.from_dict(default_payload)
            hierarchies.append(default_hierarchy)
        if default_hierarchy is None and hierarchies:
            default_hierarchy = hierarchies[0]
        if default_hierarchy is None:
            default_hierarchy = Hierarchy(name=name, elements=[], edges=[], subsets=[])

        return cls(name=name, hierarchies=hierarchies, defaultHierarchy=default_hierarchy)

    @staticmethod
    def uri_for(dimension_name: str) -> str:
        return f"Dimensions('{dimension_name}')"

    def uri(self) -> str:
        return self.uri_for(self.name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def create_dimension(tm1_service: TM1Service, dimension: Union[Dimension, str]) -> Response:
    dim_name = dimension
    if isinstance(dimension, Dimension):
        dim_name = dimension.name
    dimension_object = TM1py.Dimension(dim_name)
    logger.info(f"Creating Dimension: {dim_name}.")

    return tm1_service.dimensions.create(dimension_object)


def update_dimension(tm1_service: TM1Service, dimension: Dimension) -> Response:
    logger.info("Skipping direct Dimension update for '%s'; updates are handled by child changes.", dimension.name)
    return _build_noop_update_response(
        resource_url=format_url("/api/v1/Dimensions('{}')", dimension.name),
        message=f"No-op Dimension update for '{dimension.name}'."
    )


def delete_dimension(tm1_service: TM1Service, dimension: Dimension) -> Response:
    logger.info(f"Deleting Dimension: {dimension.name}.")
    return tm1_service.dimensions.delete(dimension.name)


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
    return str(value).replace("'", "''") if value else ""


def build_dimension_create_ti(dimension: Union[Dimension, str]) -> str:
    """
    Generates TI code to create a Dimension.
    """

    if isinstance(dimension, str):
        dim_name = dimension
    else:
        dim_name = dimension.name

    dim_clean = _escape_ti(dim_name)

    lines = []
    lines.append(f"# --- Create Dimension: {dim_clean} ---")
    lines.append(f"IF( DimensionExists('{dim_clean}') = 0 );")
    lines.append(f"    DimensionCreate('{dim_clean}');")
    lines.append(f"ENDIF;")

    return "\r\n".join(lines)


def build_dimension_update_ti(dimension: Dimension) -> str:
    """
    Generates TI placeholder code to update a Dimension.
    Contained objects are updated by their respective modules (hierarchies).
    """

    dim_clean = _escape_ti(dimension.name)
    lines = [f"# --- Update Dimension: {dim_clean} ---"]
    return "\r\n".join(lines)


def build_dimension_delete_ti(dimension: Dimension) -> str:
    """
    Generates TI code to delete a Dimension.
    """
    dim_clean = _escape_ti(dimension.name)

    lines = []
    lines.append(f"# --- Delete Dimension: {dim_clean} ---")
    lines.append(f"IF( DimensionExists('{dim_clean}') = 1 );")
    lines.append(f"    DimensionDestroy('{dim_clean}');")
    lines.append(f"ENDIF;")

    return "\r\n".join(lines)
