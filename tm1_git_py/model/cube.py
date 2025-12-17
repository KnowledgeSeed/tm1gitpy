import json
import logging
from typing import List, Any, Dict, Optional

import TM1py
from TM1_bedrock_py.bedrock import data_copy_intercube
from TM1py import TM1Service, Cube
from TM1py.Utils import format_url
from requests import Response

from tm1_git_py.model import element
from tm1_git_py.model.dimension import Dimension, create_dimension
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


def update_cube(tm1_service: TM1Service, cube: Dict[str, Any], **kwargs) -> Response:
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


def _add_dimensions_to_cube(
        tm1_service: TM1Service,
        cube_old: Cube,
        cube_new: Cube,
        dims_old: list[str],
        dims_new: list[str],
        logging_level: str = "INFO"
) -> None:
    """
    Recreate a cube with additional dimensions using tm1-bedrock-py's
    data_copy_intercube.
    When copying from the old cube into the temporary cube,
    the new dimensions are populated with either the FIRST LEAF element
    of the given dimension or a 'Legacy Data' element if no leaf is present .
    """

    cube_name = cube_old.name
    if cube_new.name != cube_old.name:
        raise ValueError(
            f"Cube name mismatch: cube_old.name={cube_old.name}, cube_new.name={cube_new.name}. "
            f"This helper expects a structural change of the same cube."
        )

    added_dims = list(set(dims_new) - set(dims_old))

    logger.info(
        f"Adding Dimensions '{added_dims}' to Cube '{cube_name}' via data_copy_intercube."
    )

    target_dim_mapping_default_elements = {}

    # 1) Determine the default element for the new dimension: FIRST LEAF
    for dim in added_dims:
        if not tm1_service.dimensions.exists(dimension_name=dim):
            create_dimension(tm1_service=tm1_service, dimension=dim)

        target_dim_mapping_default_elements[dim] = _get_first_leaf_element_name(
            tm1_service=tm1_service,
            dimension_name=dim
        )

    # 2) Create a temp cube with the new dimensionality and copy data old -> temp, forcing the new dim to first leaf.
    temp_cube_name = f"{cube_name}__tmp_add_dims"

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
        dimension_names=dims_old
    )

    logger.info(
        f"Copying data from old Cube '{cube_name}' to temporary Cube '{temp_cube_name}' "
        f"using data_copy_intercube (new dim -> first leaf '{target_dim_mapping_default_elements}')."
    )
    data_copy_intercube(
        tm1_service=tm1_service,
        data_mdx=source_mdx_old,
        target_cube_name=temp_cube_name,
        target_dim_mapping=target_dim_mapping_default_elements,
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
        dimension_names=dims_new
    )

    logger.info(
        f"Copying data from temporary Cube '{temp_cube_name}' back to final Cube '{cube_name}'."
    )
    data_copy_intercube(
        tm1_service=tm1_service,
        data_mdx=source_mdx_temp,
        target_cube_name=cube_name,
        clear_target=True,
        logging_level=logging_level
    )

    # 5) Clean up temporary cube
    logger.info(f"Deleting temporary Cube '{temp_cube_name}'.")
    tm1_service.cubes.delete(temp_cube_name)


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


def _delete_dimensions_from_cube(
        tm1_service: TM1Service,
        cube_old: Cube,
        cube_new: Cube,
        dims_old: list[str],
        dims_new: list[str],
        strategies: Optional[Dict[str, Dict[str, Any]]] = None,
        default_strategy: str = "sum_all",
        logging_level: str = "INFO"
) -> None:
    """
    Redimensionalise a cube by removing one or more dimensions (n -> n-k)
    using tm1_bedrock_py.data_copy_intercube.

    - By default (default_strategy='sum_all'):
        all deleted dimensions are aggregated away: all their elements are
        included, and duplicates in the target cube are summed.

    - `strategies` allows you to override behaviour per deleted dimension.
      Example:

        strategies = {
            "Version": {
                "strategy": "keep_element",
                "element": "Actual"
            },
            "Scenario": {
                "strategy": "keep_by_attr",
                "attr_name": "KeepOnDrop",
                "attr_value": "Y"
            }
        }

      Supported per-dimension strategies:
        - 'sum_all'       : keep all elements (aggregate across this dim)
        - 'keep_element'  : keep only a single element (e.g. 'Actual')
        - 'keep_by_attr'  : keep only elements where attribute == value

    Assumptions:
        - cube_old.name == cube_new.name
        - dims_new is a strict subset of dims_old (one or more dims removed).
        - Remaining dimensions keep their names.
    """
    strategies = strategies or {}
    cube_name = cube_old.name

    if cube_new.name != cube_old.name:
        raise ValueError(
            f"Cube name mismatch: cube_old.name={cube_old.name}, cube_new.name={cube_new.name}. "
            f"This helper expects a structural change of the same cube."
        )

    deleted_dims = list(set(dims_old) - set(dims_new))
    if not deleted_dims:
        logger.info(
            "No dimensions deleted between cube_old and cube_new. "
            "delete_dimensions_from_cube_with_bedrock has nothing to do."
        )
        return

    logger.info(
        f"Removing Dimensions {deleted_dims!r} from Cube '{cube_name}' via data_copy_intercube. "
        f"default_strategy='{default_strategy}'."
    )

    # Build MDX & source_dim_mapping based on per-dimension strategies
    per_dim_set_mdx = {}
    source_dim_mapping = {}

    for deleted_dim in deleted_dims:
        cfg = strategies.get(deleted_dim, {})
        strategy = cfg.get("strategy", default_strategy)

        if strategy == "sum_all":
            # keep all elements of this dimension → no special MDX
            logger.info(
                f"Deleted Dimension '{deleted_dim}': strategy 'sum_all' "
                f"(all elements aggregated into target)."
            )

        elif strategy == "keep_element":
            element_to_keep = cfg.get("element")
            if not element_to_keep:
                raise ValueError(
                    f"Deleted Dimension '{deleted_dim}': strategy 'keep_element' "
                    f"requires an 'element' key in strategies['{deleted_dim}']."
                )

            # Use source_dim_mapping so tm1_bedrock_py:
            # - filters to this element
            # - then drops the column
            source_dim_mapping[deleted_dim] = element_to_keep

            logger.info(
                f"Deleted Dimension '{deleted_dim}': strategy 'keep_element', "
                f"keeping only '{element_to_keep}'."
            )

        elif strategy == "keep_by_attr":
            attr_name = cfg.get("attr_name")
            attr_value = cfg.get("attr_value")

            if not attr_name or attr_value is None:
                raise ValueError(
                    f"Deleted Dimension '{deleted_dim}': strategy 'keep_by_attr' requires "
                    f"'attr_name' and 'attr_value' in strategies['{deleted_dim}']."
                )

            per_dim_set_mdx[deleted_dim] = (
                f"FILTER("
                f" TM1SUBSETALL([{deleted_dim}]), "
                f" [{deleted_dim}].CURRENTMEMBER.PROPERTIES(\"{attr_name}\") = "
                f"\"{attr_value}\""
                f")"
            )

            logger.info(
                f"Deleted Dimension '{deleted_dim}': strategy 'keep_by_attr', "
                f"keeping elements where attribute '{attr_name}' = '{attr_value}'."
            )

        else:
            raise ValueError(
                f"Deleted Dimension '{deleted_dim}': unknown strategy '{strategy}'. "
                f"Supported: 'sum_all', 'keep_element', 'keep_by_attr'."
            )

    # If we never added anything to source_dim_mapping, pass None instead
    if not source_dim_mapping:
        source_dim_mapping = None

    source_mdx_old = _build_cube_mdx_with_dim_sets(
        cube_name=cube_name,
        dimension_names=dims_old,
        per_dim_set_mdx=per_dim_set_mdx
    )

    # 1) Create a temporary cube with the reduced dimensionality and copy old -> temp
    temp_cube_name = f"{cube_name}__tmp_del_multi"

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

    logger.info(
        f"Copying data from old Cube '{cube_name}' to temporary Cube '{temp_cube_name}' "
        f"using data_copy_intercube (multiple deleted dimensions)."
    )
    data_copy_intercube(
        tm1_service=tm1_service,
        data_mdx=source_mdx_old,
        target_cube_name=temp_cube_name,
        source_dim_mapping=source_dim_mapping,
        clear_target=True,
        sum_numeric_duplicates=True,
        logging_level=logging_level
    )

    # 2) Delete the original cube and recreate it with the reduced dimension set
    logger.warning(f"Deleting original Cube '{cube_name}' before recreation.")
    delete_cube(tm1_service=tm1_service, cube_name=cube_name)

    logger.info(
        f"Recreating Cube '{cube_name}' with new Dimensions: {dims_new} and rules "
        f"from your model definition."
    )
    create_cube(tm1_service=tm1_service, cube=cube_new)

    # 3) Copy data from temp cube into the final re-created cube (dims match now)
    source_mdx_temp = _build_cube_mdx_with_dim_sets(
        cube_name=temp_cube_name,
        dimension_names=dims_new
    )

    logger.info(
        f"Copying data from temporary Cube '{temp_cube_name}' back to final Cube '{cube_name}'."
    )
    data_copy_intercube(
        tm1_service=tm1_service,
        data_mdx=source_mdx_temp,
        target_cube_name=cube_name,
        clear_target=True,
        sum_numeric_duplicates=True,
        logging_level=logging_level
    )

    # 4) Clean up temporary cube
    logger.info(f"Deleting temporary Cube '{temp_cube_name}'.")
    tm1_service.cubes.delete(temp_cube_name)
