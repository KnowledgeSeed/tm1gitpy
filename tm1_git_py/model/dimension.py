import json
import logging
from typing import List, Any, Dict
import TM1py
from TM1py.Utils import format_url
from TM1py import TM1Service, Dimension
from requests import Response
from .hierarchy import Hierarchy

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
    def __init__(self, name, hierarchies: List[Hierarchy], defaultHierarchy: Hierarchy, source_path: str):
        self.type = 'Dimension'
        self.name = name
        self.hierarchies = hierarchies
        self.defaultHierarchy = defaultHierarchy
        self.source_path = source_path

    def as_json(self):
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "Hierarchies@Code.links": [format_url("{}.hierarchies/{}.json", self.name, h) for h in self.hierarchies],
            "DefaultHierarchy": format_url("Dimensions('{}')/Hierarchies('{}')", self.name, self.defaultHierarchy.name)
        }, indent='\t')
    
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

    def to_dict(self):
        return {
            'name': self.name,
            'hierarchies': [h.to_dict() for h in self.hierarchies],
            'defaultHierarchy': self.defaultHierarchy.to_dict()
        }
        

    @staticmethod
    def as_link(name):
        # /dimensions/Dimension_A.json
        return '/dimensions/' + name


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def create_dimension(tm1_service: TM1Service, dimension: Dimension | str) -> Response:
    dim_name = dimension
    if isinstance(dimension, Dimension):
        dim_name = dimension.name
    dimension_object = TM1py.Dimension(dim_name)
    logger.info(f"Creating Dimension: {dim_name}.")

    return tm1_service.dimensions.create(dimension_object)


def update_dimension(tm1_service: TM1Service, dimension: Dict[str, Any]) -> Response:
    dimension_new = dimension.get('new')
    dimension_old = dimension.get('old')

    if tm1_service.dimensions.exists(dimension_name=dimension_new.name):
        dimension_object = tm1_service.dimensions.get(dimension_name=dimension_new.name)
        _update_dimension_hierarchies(tm1_service=tm1_service, dimension_new=dimension_new, dimension_old=dimension_old,
                                      dimension_object=dimension_object)
        return tm1_service.dimensions.update(dimension_object)
    else:
        raise ValueError(f"Cannot update Dimension: '{dimension_new.name}', Dimension does not exist")


def delete_dimension(tm1_service: TM1Service, dimension_name: str) -> Response:
    logger.info(f"Deleting Dimension: {dimension_name}.")
    return tm1_service.dimensions.delete(dimension_name)


def _update_dimension_hierarchies(
        tm1_service: TM1Service,
        dimension_new: Dimension,
        dimension_old: Dimension,
        dimension_object: TM1py.Dimension
):
    hierarchies_new = dimension_new.hierarchies
    hierarchies_old = dimension_old.hierarchies

    if hierarchies_new != hierarchies_old:
        hierarchies_to_remove = list(set(hierarchies_old) - set(hierarchies_new))
        hierarchies_to_add = list(set(hierarchies_new) - set(hierarchies_old))

        hierarchies_to_remove_names = [element.name for element in hierarchies_to_remove]
        hierarchies_to_add_names = [element.name for element in hierarchies_to_add]

        for hierarchy in hierarchies_to_remove:
            dimension_object.remove_hierarchy(hierarchy_name=hierarchy.name)
        logger.info(f"Removed Hierarchies: {hierarchies_to_remove_names} from Dimension: {dimension_new.name}.")

        for hierarchy in hierarchies_to_add:
            try:
                hierarchy_object = tm1_service.hierarchies.get(dimension_name=dimension_new.name,
                                                               hierarchy_name=hierarchy.name)
                dimension_object.add_hierarchy(hierarchy_object)
            except Exception:
                raise ValueError(f"Cannot update Dimension '{dimension_new.name}' "
                                 f"with Hierarchy: {hierarchy.name}, Hierarchy does not exist")
        logger.info(f"Added Hierarchies: {hierarchies_to_add_names} to Dimension: {dimension_new.name}.")
