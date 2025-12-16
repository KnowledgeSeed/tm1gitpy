import json
import logging
import re
from typing import List, Any, Dict

import TM1py
from networkx.classes import edges

from .edge import Edge
from .element import Element, create_element, update_element, delete_element
from .subset import Subset, create_subset, update_subset, delete_subset
from TM1py.Utils import format_url
from TM1py import TM1Service, Hierarchy
from requests import Response

# {
# 	"@type": "Hierarchy",
# 	"Name": "Capex Balance Sheet Assignment Measure",
# 	"Elements": [
# 		{
# 			"Name": "Assignment",
# 			"Type": "Numeric"
# 		},
# 		{
# 			"Name": "Comment",
# 			"Type": "String"
# 		},
# 		{
# 			"Name": "CapexName",
# 			"Type": "String"
# 		},
# 		{
# 			"Name": "BalanceSheetName",
# 			"Type": "String"
# 		},
# 		{
# 			"Name": "Value",
# 			"Type": "Numeric"
# 		}
# 	],
# 	"Subsets@Code.links": []
# }


class Hierarchy:
    def __init__(self, name, elements: List[Element], edges: List[Edge], subsets: List[Subset], source_path: str):
        self.type = 'Hierarchy'
        self.name = name
        self.elements = elements
        self.edges = edges
        self.subsets = subsets
        self.source_path = source_path

    def as_json(self):
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "Elements": [obj.__dict__ for obj in self.elements],
            "Edges": [obj.__dict__ for obj in self.edges],
            "Subsets@Code.links": [format_url("{}.subsets/{}.json", self.name, s.name) for s in self.subsets]
        }, indent='\t')

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Hierarchy):
            return NotImplemented
        
        if self.name != other.name:
            return False
        
        if set(self.elements) != set(other.elements):
            return False

        if set(self.edges) != set(other.edges):
            return False
        
        if set(self.subsets) != set(other.subsets):
            return False

        return True

    def __hash__(self) -> int:
        return hash((
            self.name,
            frozenset(self.elements),
            frozenset(self.edges),
            frozenset(self.subsets)
        ))

    def to_dict(self):
        return {
            'name': self.name,
            'elements': [e.to_dict() for e in self.elements],
            'edges': [e.to_dict() for e in self.edges],
            'subsets': [s.to_dict() for s in self.subsets]
        }

    def asLink(self, dimension_name):
        # /dimensions/Dimension_A.hierarchies/Dimension_A.json
        return '/dimensions/' + dimension_name + '.hierarchies/' + self.name + '.json'
    
    @staticmethod
    def as_link(dimension_name_base, name):
        # /dimensions/Dimension_A.json
        return '/dimensions/' + dimension_name_base + '.hierarchies/' + name


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def create_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy) -> Response:
    dimension_name = re.search(r'/(\w*)(.hierarchies)', hierarchy.source_path).group(1)
    hierarchy_object = TM1py.Hierarchy(name=hierarchy.name, dimension_name=dimension_name)
    edges = [(edge.parent, edge.name, edge.weight) for edge in hierarchy.edges]
    for parent, component, weight in edges:
        hierarchy_object.add_edge(parent, component, weight)
    response = tm1_service.hierarchies.create(hierarchy_object)
    logger.info(f"Created Hierarchy: {hierarchy.name}.")

    if response.status_code == 201:
        for element in hierarchy.elements:
            if not tm1_service.elements.exists(dimension_name, hierarchy.name, element.name):
                create_element(tm1_service=tm1_service, dimension_name=dimension_name,
                               hierarchy_name=hierarchy.name, element=element)

    return response


def update_hierarchy(tm1_service: TM1Service, hierarchy: Dict[str, Any]) -> Response:
    hierarchy_new = hierarchy.get('new')
    hierarchy_old = hierarchy.get('old')

    dimension_name = re.search(r'/(\w*)(.hierarchies)', hierarchy_new.source_path).group(1)

    if tm1_service.hierarchies.exists(dimension_name=dimension_name, hierarchy_name=hierarchy_new.name):
        hierarchy_object = tm1_service.hierarchies.get(dimension_name=dimension_name, hierarchy_name=hierarchy_new.name)

        _update_hierarchy_edges(hierarchy_new=hierarchy_new, hierarchy_old=hierarchy_old,
                                hierarchy_object=hierarchy_object)
        _update_hierarchy_elements(tm1_service=tm1_service, dimension_name=dimension_name, hierarchy_new= hierarchy_new,
                                   hierarchy_old=hierarchy_old, hierarchy_object=hierarchy_object)
        logger.info(f"Updating Hierarchy: {hierarchy_new.name}.")

        return tm1_service.hierarchies.update(hierarchy_object)

    else:
        raise ValueError(f"Cannot update Hierarchy: '{hierarchy_new.name}', Hierarchy does not exist")


def delete_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy) -> Response:
    dimension_name = re.search(r'/(\w*)(.hierarchies)', hierarchy.source_path).group(1)
    logger.info(f"Deleting Hierarchy: {hierarchy.name} of Dimension: {dimension_name}.")
    return tm1_service.hierarchies.delete(dimension_name=dimension_name, hierarchy_name=hierarchy.name)


def _update_hierarchy_elements(
        tm1_service: TM1Service,
        dimension_name: str,
        hierarchy_new: Hierarchy,
        hierarchy_old: Hierarchy,
        hierarchy_object: TM1py.Hierarchy
):
    elements_old = hierarchy_old.elements
    elements_new = hierarchy_new.elements
    if elements_old != elements_new:
        elements_to_update = list(set(elements_old) & set(elements_new))
        elements_to_remove = list(set(elements_old) - set(elements_new))
        elements_to_create = list(set(elements_new) - set(elements_old))
        
        for element in elements_to_remove:
            hierarchy_object.remove_element(element_name=element.name)
            delete_element(tm1_service=tm1_service, hierarchy_name=hierarchy_old.name,
                           dimension_name=dimension_name, element_name=element.name)
        logger.debug(f"Removed Elements: {elements_to_remove} from Hierarchy: {hierarchy_new.name}.")

        for element in elements_to_update:
            update_element(tm1_service=tm1_service, hierarchy_name=hierarchy_new.name,
                           dimension_name=dimension_name, element=element)
            hierarchy_object.update_element(element_name=element.name, element_type=element.type)
        logger.debug(f"Updated Elements: {elements_to_update} in Hierarchy: {hierarchy_new.name}.")

        for element in elements_to_create:
            create_element(tm1_service=tm1_service, hierarchy_name=hierarchy_new.name,
                           dimension_name=dimension_name, element=element)
            element_object = tm1_service.elements.get(dimension_name=dimension_name,
                                                      hierarchy_name=hierarchy_new.name, element_name=element.name)
            if element_object not in hierarchy_object.elements.values():
                hierarchy_object.add_element(element_name=element.name, element_type=element.type)
        logger.debug(f"Added Elements: {elements_to_create} to Hierarchy: {hierarchy_new.name}.")


def _update_hierarchy_edges(
        hierarchy_new: Hierarchy,
        hierarchy_old: Hierarchy,
        hierarchy_object: TM1py.Hierarchy
):
    edges_new = {(edge.parent, edge.name, edge.weight) for edge in hierarchy_new.edges}
    edges_old = {(edge.parent, edge.name, edge.weight) for edge in hierarchy_old.edges}

    if edges_old != edges_new:
        edges_to_remove = list(edges_old - edges_new)
        edges_to_add = list(edges_new - edges_old)

        for parent, component, weight in edges_to_remove:
            hierarchy_object.remove_edge(parent, component)
        logger.debug(f"Removed Edges: {edges_to_remove} from Hierarchy: {hierarchy_new.name}.")

        for parent, component, weight in edges_to_add:
            hierarchy_object.add_edge(parent, component, weight)
        logger.debug(f"Added Edges: {edges_to_add} from Hierarchy: {hierarchy_new.name}.")
