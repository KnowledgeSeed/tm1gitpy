import json
import logging
from typing import List, Any, Dict

import TM1py
from TM1py import TM1Service, Cube
from requests import Response
from TM1_bedrock_py.bedrock import data_copy_intercube

from . import mdxview
from .dimension import Dimension
from .element import Element
from .hierarchy import Hierarchy
from .mdxview import MDXView
from .subset import Subset
from TM1py.Utils import format_url
from .rule import Rule


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
    def __init__(self, name, dimensions: List[Dimension], rules: list[Rule], views: List[MDXView], source_path: str):
        self.type = 'Cube'
        self.name = name
        self.dimensions = dimensions
        self.rules = rules
        self.views = views
        self.source_path = source_path

    def as_json(self):
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "Dimensions": [{"@id": format_url("Dimensions('{}')", d.name)} for d in self.dimensions],
            "Rules@Code.link": format_url("{}.rules", self.name),
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

    def to_dict(self):
        return {
            'name': self.name,
            'dimensions': [d.to_dict() for d in self.dimensions],
            'rules': [r.__dict__ for r in self.rules],
            'views': [v.to_dict() for v in self.views]
        }

    @staticmethod
    def as_link(name):
        # /cubes/Cube_A.json
        # /cubes/Cube_A.rules
        return '/cubes/' + name


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


def update_cube(tm1_service: TM1Service, cube: Dict[str, Any]) -> Response:
    cube_new = cube.get('new')
    cube_old = cube.get('old')
    if tm1_service.cubes.exists(cube_name=cube_new.name):
        dimensions_new = [d.name for d in cube_new.dimensions]
        dimensions_old = [d.name for d in cube_old.dimensions]
        cube_object = tm1_service.cubes.get(cube_new.name)

        if dimensions_new != dimensions_old:
            if set(dimensions_new) == set(dimensions_old):

                tm1_service.cubes.update_storage_dimension_order(
                    cube_name=cube_new.name, dimension_names=dimensions_new)
                logger.info(f"Updated Dimension order for Cube: {cube_new.name}.")
            else:
                # TODO: data_copy_intercube to temp
                old_dims = [d.name for d in cube_old.dimensions]
                added_dims = set(d.name for d in cube_new.dimensions) - set(old_dims)
                if added_dims:
                    pass
                logger.warning(f"Dimensions for Cube: {cube_new.name} changed. Cube will be recreated to match new Dimension set.")
                #delete_cube(tm1_service=tm1_service, cube_name=cube_new.name)
                #return create_cube(tm1_service=tm1_service, cube=cube_new)

        _update_cube_views(tm1_service=tm1_service, cube_new=cube_new, cube_old=cube_old)

        new_rule_text = cube_new.get_rule_text()
        if cube_object.rules.body != new_rule_text:
            cube_object.rules._text = new_rule_text
            logger.info(f"Updated Rules for Cube: {cube_new.name}.")

        return tm1_service.cubes.update(cube_object)
    else:
        raise ValueError(f"Cannot update Cube: '{cube_new.name}', Cube does not exist")


def delete_cube(tm1_service: TM1Service, cube_name: str) -> Response:
    logger.warning(f"Deleting Cube: {cube_name}.")
    return tm1_service.cubes.delete(cube_name)


def _update_cube_views(tm1_service: TM1Service, cube_new: Cube, cube_old: Cube):
    views_new = cube_new.views
    views_old = cube_old.views

    if views_new != views_old:

        views_to_update = list(set(views_old) & set(views_new))
        views_to_add = list(set(views_new) - set(views_old))
        views_to_remove = list(set(views_old) - set(views_new))

        for view in views_to_add:
            mdxview.create_mdx_view(tm1_service=tm1_service, cube_name=cube_new.name, mdx_view=view)
        for view in views_to_update:
            mdxview.update_mdx_view(tm1_service=tm1_service, cube_name=cube_new.name, mdx_view=view)
        for view in views_to_remove:
            mdxview.delete_mdx_view(tm1_service=tm1_service, cube_name=cube_new.name, mdx_view_name=view.name)

        logger.info(f"Updated Views for Cube: {cube_new.name}.")
