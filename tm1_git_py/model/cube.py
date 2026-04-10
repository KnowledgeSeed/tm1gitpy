import json
import logging
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
from tm1_git_py.model.rule import Rule


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
class Cube:
    def __init__(self, name, dimensions: List[Dimension], rules: List[Rule], views: List[MDXView]):
        self.type = 'Cube'
        self.name = name
        self.dimensions = dimensions
        self.rules = rules
        self.views = views

    def as_json(self):
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "Dimensions": [{"@id": format_url("Dimensions('{}')", d.name)} for d in self.dimensions],
            "Rules@Code.link": format_url("{}.rules", self.name) if self.rules else [],
            "Views@Code.links": [format_url("{}.views/{}.json", self.name, v.name) for v in self.views],
        }, indent='\t')

    def get_rule_text(self) -> str:
        if not self.rules: return ""
        content_parts = []
        for rule in self.rules:
            if rule.comment:
                content_parts.append(rule.comment)
            content_parts.append(rule.full_statement)
        return "\n\n".join(content_parts)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Cube):
            return NotImplemented

        if self.name != other.name:
            return False
        if sorted([d.name for d in self.dimensions]) != sorted([d.name for d in other.dimensions]):
            return False
        if set(self.views) != set(other.views):
            return False
        if set(self.rules) != set(other.rules):
            return False
        return True

    def __hash__(self) -> int:
        return hash((
            self.name,
            tuple(sorted([d.name for d in self.dimensions])),
            frozenset(self.rules),
            frozenset(self.views)
        ))

    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self):
        return {
            'name': self.name,
            'dimensions': [d.to_dict() for d in self.dimensions],
            'rules': [r.to_dict() for r in self.rules],
            'views': [v.to_dict() for v in self.views]
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any]
    ) -> "Cube":

        name = data.get("name") or data.get("Name")
        dimension_payloads = data.get("dimensions") or data.get("Dimensions") or []
        dimensions = [Dimension.from_dict(payload) for payload in dimension_payloads]

        rule_payloads = data.get("rules") or data.get("Rules") or []
        rule_base_path = f"cubes/{name}"
        rules = [
            Rule.from_dict(payload, source_path=f"{rule_base_path}.rules", cube_name=name)
            for payload in rule_payloads
        ]

        view_payloads = data.get("views") or data.get("Views") or []
        views = [
            MDXView.from_dict(payload, cube_name=name)
            for payload in view_payloads
        ]
        return cls(name=name, dimensions=dimensions, rules=rules, views=views)

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
    dimensions = [dim.name for dim in cube.dimensions]
    rule_text = cube.get_rule_text()
    cube_object = TM1py.Cube(cube.name, dimensions, rule_text)

    logger.info(f"Creating Cube: {cube.name} with Dimensions: {dimensions} and Rules: {cube.rules}.")
    return tm1_service.cubes.create(cube_object)


def update_cube(tm1_service: TM1Service, cube: Cube) -> Response:
    cube_object = tm1_service.cubes.get(cube.name)

    #dimensions_new = [d.name for d in cube.dimensions]
    #dimensions_old = [d.name for d in cube_old.dimensions]
    """
    if dimensions_new != dimensions_old:
        if set(dimensions_new) == set(dimensions_old):
            tm1_service.cubes.update_storage_dimension_order(
                cube_name=cube_new.name, dimension_names=dimensions_new)
            logger.info(f"Updated Dimension order for Cube: {cube_new.name}.")
        else:
            logger.warning(f"Dimensions for Cube: {cube_new.name} changed. Cube will be recreated to match new Dimension set.")
            added_dims = list(set(dimensions_new) - set(dimensions_old))
            removed_dims = list(set(dimensions_old) - set(dimensions_new))

            if added_dims:
                _add_dimensions_to_cube(
                    tm1_service=tm1_service,
                    cube_old=cube_old,
                    cube_new=cube_new,
                    dims_old=dimensions_old,
                    dims_new=dimensions_new
                )
                cube_object = tm1_service.cubes.get(cube_new.name)
            elif removed_dims:
                _delete_dimensions_from_cube(
                    tm1_service=tm1_service,
                    cube_old=cube_old,
                    cube_new=cube_new,
                    dims_old=dimensions_old,
                    dims_new=dimensions_new,
                    **kwargs
                )
                cube_object = tm1_service.cubes.get(cube_new.name)
    """
    new_rule_text = cube.get_rule_text()
    if not cube_object.rules or cube_object.rules.body != new_rule_text:
        cube_object.rules = TM1py.Rules(new_rule_text)
        tm1_service.cubes.update_or_create_rules(cube_name=cube.name, rules=new_rule_text.strip())
        logger.info(f"Updated Rules for Cube: {cube.name}.")

    return tm1_service.cubes.update(cube_object)


def delete_cube(tm1_service: TM1Service, cube: Cube) -> Response:
    logger.warning(f"Deleting Cube: {cube.name}.")
    return tm1_service.cubes.delete(cube.name)


# ------------------------------------------------------------------------------------------------------------
# Functions to recreate Cube if Dimensions changed. Uses tm1-bedrock-py's data_copy_intercube to move data to new cube
# ------------------------------------------------------------------------------------------------------------

def _build_full_cube_mdx(
        cube_name: str,
        dimension_names: List[str],
) -> str:

    subset_mdx_list = "\n * ".join(f"{{ TM1SUBSETALL([{dim}]) }}" for dim in dimension_names)
    mdx = f"""
        SELECT NON EMPTY
            {subset_mdx_list}
             ON 0,
        FROM [{cube_name}]
        """

    return mdx.strip()


def _get_first_leaf_element_name(tm1_service: TM1Service, dimension_name: str) -> str:
    """
    Return the name of the first leaf element in the default hierarchy
    (hierarchy with the same name as the dimension).
    """
    hierarchy = tm1_service.hierarchies.get(
        dimension_name=dimension_name,
        hierarchy_name=dimension_name
    )
    leaf_names = [
        elem.name
        for elem in hierarchy.elements.values()
        if elem.element_type != "Consolidated"
    ]

    if not leaf_names:
        default_elem_name = "Legacy Data"
        default_elem_type = "Numeric"
        element.create_element(
            tm1_service=tm1_service,
            hierarchy_name=dimension_name,
            dimension_name=dimension_name,
            element=TM1py.Element(name=default_elem_name, element_type=default_elem_type)
        )
        hierarchy.add_element(element_name=default_elem_name, element_type=default_elem_type)
        tm1_service.hierarchies.update(hierarchy=hierarchy)
        first_leaf = "Legacy Data"
    else:
        first_leaf = leaf_names[0]

    logger.info(
        f"Using first leaf element '{first_leaf}' as default for Dimension '{dimension_name}'."
    )
    return first_leaf

def _build_cube_mdx_with_dim_sets(
        cube_name: str,
        dimension_names: List[str],
        per_dim_set_mdx: Optional[Dict[str, str]] = None
) -> str:
    """
    Build a generic MDX that cross-joins all dimensions on a single axis.

    `per_dim_set_mdx` can override the default set expression for specific
    dimensions. Values must be valid MDX set expressions for the dimension.
    Example:
        per_dim_set_mdx = {
            "Version": "{[Version].[Actual]}",
            "Scenario": "FILTER(TM1SUBSETALL([Scenario]), ...)"
        }
    """
    per_dim_set_mdx = per_dim_set_mdx or {}

    set_exprs = []
    for dim in dimension_names:
        if dim in per_dim_set_mdx:
            set_exprs.append(per_dim_set_mdx[dim])
        else:
            set_exprs.append(f"{{ TM1SUBSETALL([{dim}]) }}")

    subset_mdx_list = "\n * ".join(set_exprs)

    mdx = f"""
        SELECT NON EMPTY
            {subset_mdx_list}
        ON 0
        FROM [{cube_name}]
    """
    return mdx.strip()
