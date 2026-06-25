import re
import uuid

import TM1py
import pytest
from TM1py import TM1Service

from test_integration.test_base import (
    execute_ephemeral_ti,
    tm1_service,
)
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.element import Element, build_element_update_ti
from tm1_git_py.services.apply import build_master_changeset_ti
from tm1_git_py.services.changeset import Change, ChangeType, Changeset, ObjectType


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _set_source_path(obj, path: str):
    setattr(obj, "source_path", path)
    return obj


def _uri_from_source_path(obj) -> str:
    text = (getattr(obj, "source_path", "") or "").replace("\\", "/")

    match = re.match(r"^dimensions/([^/]+)\.json$", text, flags=re.IGNORECASE)
    if match:
        dim_name = match.group(1)
        return f"Dimensions('{dim_name}')"

    match = re.match(r"^dimensions/([^/]+)\.hierarchies/([^/]+)\.json$", text, flags=re.IGNORECASE)
    if match:
        dim_name, hier_name = match.groups()
        return f"Dimensions('{dim_name}')/Hierarchies('{hier_name}')"

    match = re.match(r"^dimensions/([^/]+)\.hierarchies/([^/]+)\.json/([^/]+)$", text, flags=re.IGNORECASE)
    if match:
        dim_name, hier_name, elem_name = match.groups()
        return f"Dimensions('{dim_name}')/Hierarchies('{hier_name}')/Elements('{elem_name}')"

    match = re.match(r"^dimensions/([^/]+)\.hierarchies/([^/]+)\.subsets/([^/]+)\.json$", text, flags=re.IGNORECASE)
    if match:
        dim_name, hier_name, subset_name = match.groups()
        return f"Dimensions('{dim_name}')/Hierarchies('{hier_name}')/Subsets('{subset_name}')"

    match = re.match(r"^cubes/([^/]+)\.views/([^/]+)\.json$", text, flags=re.IGNORECASE)
    if match:
        cube_name, view_name = match.groups()
        return f"Cubes('{cube_name}')/Views('{view_name}')"

    match = re.match(r"^cubes/([^/]+)\.json$", text, flags=re.IGNORECASE)
    if match:
        cube_name = match.group(1)
        return f"Cubes('{cube_name}')"

    raise ValueError(f"Unable to derive uri from source_path: '{text}'")


def _create_dimension_with_default_hierarchy(
        tm1: TM1Service,
        dim_name: str,
        elements: list[tuple[str, str]],
):
    dim = TM1py.Dimension(dim_name)
    hier = TM1py.Hierarchy(dimension_name=dim_name, name=dim_name)
    for element_name, element_type in elements:
        hier.add_element(element_name, element_type)
    dim.add_hierarchy(hier)
    tm1.dimensions.update_or_create(dim)
        

@pytest.mark.usefixtures("tm1_service")
class TestTIAtomicity:

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service: TM1Service = tm1_service

    def test_fail_fast_rollback_dimension_is_not_persisted(self):
        dim_valid = _uid("Atomicity_Test_Dim")
        cube_invalid = _uid("Atomicity_Test_Cube")
        missing_dim = _uid("Non_Existent_Dim")

        valid_dim_obj = _set_source_path(
            Dimension(name=dim_valid, hierarchies=[], defaultHierarchy=None),
            f"dimensions/{dim_valid}.json",
        )
        invalid_cube_obj = _set_source_path(
            Cube(
                name=cube_invalid,
                dimensions=[dim_valid, missing_dim],
                rules=[],
                views=[],
            ),
            f"cubes/{cube_invalid}.json",
        )

        changeset = Changeset("ti_p4_fail_fast_rollback")
        changeset.changes = [
            Change(ChangeType.ADD, ObjectType.DIMENSION, valid_dim_obj.source_path, valid_dim_obj),
            Change(ChangeType.ADD, ObjectType.CUBE, invalid_cube_obj.source_path, invalid_cube_obj),
        ]
        master_ti = build_master_changeset_ti(changeset)

        with pytest.raises(Exception):
            execute_ephemeral_ti(self.tm1_service, master_ti)

        assert not self.tm1_service.dimensions.exists(dim_valid)

        if self.tm1_service.cubes.exists(cube_invalid):
            self.tm1_service.cubes.delete(cube_invalid)
        if self.tm1_service.dimensions.exists(dim_valid):
            self.tm1_service.dimensions.delete(dim_valid)

    def test_element_type_data_loss_prevention_recreate_clears_old_numeric_value(self):
        base_dim = _uid("TI_P4_BASE_DIM")
        measure_dim = _uid("TI_P4_MEASURE_DIM")
        cube_name = _uid("TI_P4_CUBE")
        row_el = "Row1"
        measure_el = "MetricA"

        source_path = f"dimensions/{measure_dim}.hierarchies/{measure_dim}.json/{measure_el}"
        measure_as_string = _set_source_path(Element(name=measure_el, type="String"), source_path)

        try:
            _create_dimension_with_default_hierarchy(self.tm1_service, base_dim, [(row_el, "Numeric")])
            _create_dimension_with_default_hierarchy(self.tm1_service, measure_dim, [(measure_el, "Numeric")])
            self.tm1_service.cubes.create(TM1py.Cube(name=cube_name, dimensions=[base_dim, measure_dim], rules=""))

            self.tm1_service.cells.write_value(
                value=100,
                cube_name=cube_name,
                element_tuple=[row_el, measure_el],
            )
            before_value = self.tm1_service.cells.get_value(
                cube_name=cube_name,
                elements=[(base_dim, row_el), (measure_dim, measure_el)],
            )
            assert float(before_value) == 100.0

            update_ti = build_element_update_ti(measure_as_string, uri=_uri_from_source_path(measure_as_string))
            execute_ephemeral_ti(self.tm1_service, update_ti)

            updated_element = self.tm1_service.elements.get(
                dimension_name=measure_dim,
                hierarchy_name=measure_dim,
                element_name=measure_el,
            )
            assert str(getattr(updated_element, "element_type", "")).lower().startswith("string")

            after_value = self.tm1_service.cells.get_value(
                cube_name=cube_name,
                elements=[(base_dim, row_el), (measure_dim, measure_el)],
            )
            assert str(after_value) not in {"100", "100.0"}
        finally:
            if self.tm1_service.cubes.exists(cube_name):
                self.tm1_service.cubes.delete(cube_name)
            if self.tm1_service.dimensions.exists(base_dim):
                self.tm1_service.dimensions.delete(base_dim)
            if self.tm1_service.dimensions.exists(measure_dim):
                self.tm1_service.dimensions.delete(measure_dim)
