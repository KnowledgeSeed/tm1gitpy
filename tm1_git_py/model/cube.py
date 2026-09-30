import json
import logging
import re
from typing import List, Any, Dict, Optional

import TM1py
from TM1py import TM1Service, Cube
from TM1py.Utils import format_url
# from TM1_bedrock_py.bedrock import data_copy_intercube
from requests import Response

from tm1_git_py.model import element
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.dimension import create_dimension
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.rule import Rule, extract_cube_rule_text, merge_rule_text


# {
# 	"@type":"Cube",
# 	"Name":"Channel Csoportos Flat Assignment",
# 	"Dimensions":
# 	[
# 		{
# 			"@id":"Dimensions('Version')"
# 		},
# 		{
# 			"@id":"Dimensions('Period')"
# 		},
# 		{
# 			"@id":"Dimensions('Channel')"
# 		},
# 		{
# 			"@id":"Dimensions('Csoportos Flat')"
# 		},
# 		{
# 			"@id":"Dimensions('Channel Csoportos Flat Assignment Measure')"
# 		}
# 	],
# 	"Views@Code.links":
# 	[
# 		"Channel Csoportos Flat Assignment.views/CsoportosFlatSubsetTechnical.json"
# 	]
# }
def _dimension_name_from_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Dimension):
        return payload.name
    if isinstance(payload, dict):
        name = payload.get("name") or payload.get("Name")
        if name:
            return str(name)
        dimension_id = payload.get("@id")
        if isinstance(dimension_id, str):
            match = re.search(r"Dimensions\('([^']*)'\)", dimension_id)
            if match:
                return match.group(1)
    raise ValueError(f"Unable to resolve cube dimension name from payload: {payload!r}")


class Cube:
    def __init__(
            self,
            name,
            dimensions: List[str],
            rules: List[Rule],
            views: List[MDXView],
            drillthrough_rules: Optional[List[Rule]] = None,
    ):
        self.type = 'Cube'
        self.name = name
        self.dimensions = dimensions
        self.rules = rules
        self.views = views
        self.drillthrough_rules = drillthrough_rules or []

    def as_json(self):
        payload: Dict[str, Any] = {
            "@type": self.type,
            "Name": self.name,
            "Dimensions": [
                {"@id": format_url("Dimensions('{}')", dimension_name)}
                for dimension_name in self.dimensions
            ],
        }
        if self.rules:
            payload["Rules@Code.link"] = format_url("{}.rules", self.name)
        if self.drillthrough_rules:
            payload["DrillthroughRules@Code.link"] = format_url("{}.drillthrough.rules", self.name)
        payload["Views@Code.links"] = [
            format_url("{}.views/{}.json", self.name, v.name) for v in self.views
        ]
        s = json.dumps(payload, indent="\t", separators=(",", ":"))
        return s.replace(":[\n", ":\n\t[\n")

    def get_rule_text(self) -> str:
        return self._rule_text(self.rules)

    def get_drillthrough_rule_text(self) -> str:
        return self._rule_text(self.drillthrough_rules)

    @staticmethod
    def _rule_text(rules: List[Rule]) -> str:
        if not rules: return ""
        return "".join(rule.full_statement for rule in rules)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Cube):
            return NotImplemented

        if self.name != other.name:
            return False
        if sorted(self.dimensions) != sorted(other.dimensions):
            return False
        if set(self.views) != set(other.views):
            return False
        if set(self.rules) != set(other.rules):
            return False
        if set(self.drillthrough_rules) != set(other.drillthrough_rules):
            return False
        return True

    def __hash__(self) -> int:
        return hash((
            self.name,
            tuple(sorted(self.dimensions)),
            frozenset(self.rules),
            frozenset(self.drillthrough_rules),
            frozenset(self.views)
        ))

    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self):
        return {
            'name': self.name,
            'dimensions': list(self.dimensions),
            'rules': [r.to_dict() for r in self.rules],
            'drillthrough_rules': [r.to_dict() for r in self.drillthrough_rules],
            'views': [v.to_dict() for v in self.views]
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any]
    ) -> "Cube":

        name = data.get("name") or data.get("Name")
        dimension_payloads = data.get("dimensions") or data.get("Dimensions") or []
        dimensions = [_dimension_name_from_payload(payload) for payload in dimension_payloads]

        rule_payloads = data.get("rules") or data.get("Rules") or []
        rules = [
            Rule.from_dict(payload)
            for payload in rule_payloads
        ]
        drillthrough_rule_payloads = (
            data.get("drillthrough_rules")
            or data.get("drillthroughRules")
            or data.get("DrillthroughRules")
            or []
        )
        drillthrough_rules = [
            Rule.from_dict(payload)
            for payload in drillthrough_rule_payloads
        ]

        view_payloads = data.get("views") or data.get("Views") or []
        views = [
            MDXView.from_dict(payload, cube_name=name)
            for payload in view_payloads
        ]
        return cls(
            name=name,
            dimensions=dimensions,
            rules=rules,
            views=views,
            drillthrough_rules=drillthrough_rules,
        )

    @staticmethod
    def uri_for(cube_name: str) -> str:
        return f"Cubes('{cube_name}')"

    def uri(self) -> str:
        return self.uri_for(self.name)

# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def create_cube(tm1_service: TM1Service, cube: Cube) -> Response:
    dimensions = list(cube.dimensions)
    rule_text = cube.get_rule_text()
    cube_object = TM1py.Cube(cube.name, dimensions, rule_text)

    logger.info(f"Creating Cube: {cube.name} with Dimensions: {dimensions} and Rules: {cube.rules}.")
    return tm1_service.cubes.create(cube_object)


def _unchanged_cube_response(cube_name: str, reason: str) -> Response:
    response = Response()
    response.status_code = 200
    response.url = Cube.uri_for(cube_name)
    response._content = f"Cube '{cube_name}' unchanged: {reason}.".encode("utf-8")
    response.encoding = "utf-8"
    return response


def update_cube(tm1_service: TM1Service, cube: Cube) -> Response:
    """Write the cube's rule changes onto the target cube's current rules.

    ``cube.rules`` is a delta of rule segments (see ``merge_rule_text``): an
    empty list leaves the target rules untouched. The dimension set of a cube
    cannot change, so a differing ``cube.dimensions`` is only reported.
    """
    target = tm1_service.cubes.get(cube.name)

    target_dimensions = list(getattr(target, "dimensions", None) or [])
    if cube.dimensions and set(cube.dimensions) != set(target_dimensions):
        logger.warning(
            "Dimensions of Cube '%s' differ from the target (%s -> %s); "
            "changing the dimension set of a cube is not supported and is not applied.",
            cube.name, target_dimensions, list(cube.dimensions),
        )

    if not cube.rules:
        logger.info("No rule changes for Cube '%s'; skipping rules write.", cube.name)
        return _unchanged_cube_response(cube.name, "no rule changes")

    target_text = extract_cube_rule_text(target)
    new_text = merge_rule_text(target_text, cube.name, cube.rules)
    if new_text == target_text:
        logger.info("Rules of Cube '%s' unchanged; skipping rules write.", cube.name)
        return _unchanged_cube_response(cube.name, "rules unchanged")

    logger.info("Updating Rules for Cube '%s' (%d rule segment change(s)).", cube.name, len(cube.rules))
    return tm1_service.cubes.update_or_create_rules(cube_name=cube.name, rules=new_text)


def delete_cube(tm1_service: TM1Service, cube: Cube) -> Response:
    logger.warning(f"Deleting Cube: {cube.name}.")
    return tm1_service.cubes.delete(cube.name)
