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
    if tm1_service.cubes.exists(cube_name=cube_new.name) and cube_new.name == cube_old.name:
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
                added_dims = list(set(dimensions_new) - set(dimensions_old))
                if len(added_dims) == 1:
                    pass
                    """
                    _add_dimension_to_cube(
                        tm1_service=tm1_service,
                        cube_old=cube_old,
                        cube_new=cube_new,
                        new_dimension_name=added_dims[0].name,
                        measure_dimension_name=""
                    )
                    """
                logger.warning(f"Dimensions for Cube: {cube_new.name} changed. Cube will be recreated to match new Dimension set.")
                #delete_cube(tm1_service=tm1_service, cube_name=cube_new.name)
                #return create_cube(tm1_service=tm1_service, cube=cube_new)

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


def _build_full_cube_mdx(
        cube_name: str,
        dimension_names: List[str],
        measure_dimension_name: str
) -> str:
    """
    Build a simple MDX that reads all data from a cube, assuming there is a
    single 'measure' dimension (measure_dimension_name) and all other
    dimensions should be fully cross-joined on rows.

    This is intentionally simple and generic. For large cubes you may want
    to restrict subsets instead of TM1SUBSETALL everywhere.
    """
    non_measure_dims = [dim for dim in dimension_names if dim != measure_dimension_name]

    if not non_measure_dims:
        raise ValueError("At least one non-measure dimension is required to build MDX.")

    rows_mdx = " * ".join(f"TM1SUBSETALL([{dim}])" for dim in non_measure_dims)

    mdx = f"""
        SELECT
            {{ TM1SUBSETALL([{measure_dimension_name}]) }} ON COLUMNS,
            {{ {rows_mdx} }} ON ROWS
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
        raise ValueError(
            f"Dimension '{dimension_name}' has no leaf elements; "
            f"cannot determine default target element for redimensionalisation."
        )

    leaf_names.sort()
    first_leaf = leaf_names[0]
    logger.info(
        f"Using first leaf element '{first_leaf}' as default for Dimension '{dimension_name}'."
    )
    return first_leaf


def _add_dimension_to_cube(
        tm1_service: TM1Service,
        cube_old: Cube,
        cube_new: Cube,
        new_dimension_name: str,
        measure_dimension_name: str,
        logging_level: str = "INFO"
) -> None:
    """
    Recreate a cube with an additional dimension using tm1-bedrock-py's
    data_copy_intercube. When copying from the old (n-dim) cube into the
    temporary (n+1-dim) cube, the new dimension is always populated with
    the FIRST LEAF element of that dimension.

    Assumptions:
    - cube_old.name == cube_new.name (same logical cube, changed structure).
    - Exactly one new dimension is added.
    - The new dimension exists in TM1 (with at least one leaf) before this runs.
    - measure_dimension_name is the name of the 'measure' dimension.
    """

    cube_name = cube_old.name
    if cube_new.name != cube_old.name:
        raise ValueError(
            f"Cube name mismatch: cube_old.name={cube_old.name}, cube_new.name={cube_new.name}. "
            f"This helper expects a structural change of the same cube."
        )

    dims_old = [d.name for d in cube_old.dimensions]
    dims_new = [d.name for d in cube_new.dimensions]

    added_dims = list(set(dims_new) - set(dims_old))
    if len(added_dims) != 1:
        raise ValueError(
            f"Expected exactly one new dimension to be added, got {added_dims!r} "
            f"(old={dims_old}, new={dims_new})"
        )

    logger.info(
        f"Adding Dimension '{new_dimension_name}' to Cube '{cube_name}' via data_copy_intercube."
    )

    # 1) Determine the default element for the new dimension: FIRST LEAF
    default_new_element = _get_first_leaf_element_name(
        tm1_service=tm1_service,
        dimension_name=new_dimension_name
    )

    mapping_steps_first_leaf = [
        {
            "method": "replace",
            "mapping": {
                new_dimension_name: {
                    "*": default_new_element
                }
            }
        }
    ]

    # 2) Create a temp cube with the new dimensionality and copy data old -> temp, forcing the new dim to first leaf.
    temp_cube_name = f"{cube_name}__tmp_add_{new_dimension_name}"

    if tm1_service.cubes.exists(temp_cube_name):
        logger.warning(f"Temporary Cube '{temp_cube_name}' already exists. Deleting it.")
        tm1_service.cubes.delete(temp_cube_name)

    temp_cube_object = TM1py.Cube(
        name=temp_cube_name,
        dimensions=dims_new,
        rules=""
    )
    logger.info(
        f"Creating temporary Cube '{temp_cube_name}' with Dimensions: {dims_new}."
    )
    tm1_service.cubes.create(temp_cube_object)

    source_mdx_old = _build_full_cube_mdx(
        cube_name=cube_name,
        dimension_names=dims_old,
        measure_dimension_name=measure_dimension_name
    )

    logger.info(
        f"Copying data from old Cube '{cube_name}' to temporary Cube '{temp_cube_name}' "
        f"using data_copy_intercube (new dim -> first leaf '{default_new_element}')."
    )
    data_copy_intercube(
        tm1_service=tm1_service,
        data_mdx=source_mdx_old,
        target_cube_name=temp_cube_name,
        mapping_steps=mapping_steps_first_leaf,
        clear_target=True,
        logging_level=logging_level
    )

    # 3) Delete the original cube and recreate it with the new dimensionality
    logger.warning(f"Deleting original Cube '{cube_name}' before recreation.")
    delete_cube(tm1_service=tm1_service, cube_name=cube_name)

    logger.info(
        f"Recreating Cube '{cube_name}' with new Dimensions: {dims_new} and rules "
        f"from your model definition."
    )
    create_cube(tm1_service=tm1_service, cube=cube_new)

    # 4) Copy data from temp cube into the final re-created cube
    source_mdx_temp = _build_full_cube_mdx(
        cube_name=temp_cube_name,
        dimension_names=dims_new,
        measure_dimension_name=measure_dimension_name
    )

    logger.info(
        f"Copying data from temporary Cube '{temp_cube_name}' back to final Cube '{cube_name}'."
    )
    data_copy_intercube(
        tm1_service=tm1_service,
        data_mdx=source_mdx_temp,
        target_cube_name=cube_name,
        mapping_steps=None,
        clear_target=True,
        logging_level=logging_level
    )

    # 5) Clean up temporary cube
    logger.info(f"Deleting temporary Cube '{temp_cube_name}'.")
    tm1_service.cubes.delete(temp_cube_name)
