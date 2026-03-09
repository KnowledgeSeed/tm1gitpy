import uuid

import pytest
import TM1py
from TM1py import TM1Service

from test_integration.test_base import execute_ephemeral_ti, tm1_service
from tm1_git_py.model.cube import Cube, build_cube_create_ti, build_cube_delete_ti, build_cube_update_ti
from tm1_git_py.model.dimension import Dimension, build_dimension_create_ti, build_dimension_delete_ti
from tm1_git_py.model.edge import Edge, build_edge_create_ti, build_edge_delete_ti, build_edge_update_ti
from tm1_git_py.model.element import Element, build_element_create_ti, build_element_delete_ti, build_element_update_ti
from tm1_git_py.model.hierarchy import Hierarchy, build_hierarchy_create_ti
from tm1_git_py.model.mdxview import MDXView, build_mdxview_create_ti, build_mdxview_delete_ti, build_mdxview_update_ti
from tm1_git_py.model.nativeview import NativeView, build_native_view_create_ti, build_native_view_delete_ti, \
    build_native_view_update_ti
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.subset import Subset, build_subset_create_ti, build_subset_delete_ti, build_subset_update_ti


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _view_exists(tm1: TM1Service, cube_name: str, view_name: str) -> bool:
    exists = tm1.views.exists(cube_name=cube_name, view_name=view_name)
    if isinstance(exists, (tuple, list)):
        return any(exists)
    return bool(exists)


def _create_dimension_with_default_hierarchy(tm1: TM1Service, dim_name: str):
    dim = TM1py.Dimension(dim_name)
    dim.add_hierarchy(TM1py.Hierarchy(dimension_name=dim_name, name=dim_name))
    tm1.dimensions.update_or_create(dim)


@pytest.mark.usefixtures("tm1_service")
class TestTICRUDIntegration:

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service: TM1Service = tm1_service

    def test_dimension_create_and_delete_via_ephemeral_ti(self):
        dim_name = _uid("TI_P2_DIM")
        dim_stub = Dimension(name=dim_name, hierarchies=[], defaultHierarchy=None, source_path=f"dimensions/{dim_name}.json")

        try:
            create_ti = build_dimension_create_ti(dim_name)
            execute_ephemeral_ti(self.tm1_service, create_ti)
            assert self.tm1_service.dimensions.exists(dim_name)
            assert self.tm1_service.hierarchies.exists(dim_name, dim_name)

            delete_ti = build_dimension_delete_ti(dim_stub)
            execute_ephemeral_ti(self.tm1_service, delete_ti)
            assert not self.tm1_service.dimensions.exists(dim_name)
        finally:
            if self.tm1_service.dimensions.exists(dim_name):
                self.tm1_service.dimensions.delete(dim_name)

    def test_hierarchy_create_named_hierarchy_via_ephemeral_ti(self):
        dim_name = _uid("TI_P2_HDIM")
        hier_name = _uid("HIER")
        hierarchy_obj = Hierarchy(
            name=hier_name,
            elements=[],
            edges=[],
            subsets=[],
            source_path=f"dimensions/{dim_name}.hierarchies/{hier_name}.json",
        )

        try:
            _create_dimension_with_default_hierarchy(self.tm1_service, dim_name)
            assert not self.tm1_service.hierarchies.exists(dim_name, hier_name)

            create_ti = build_hierarchy_create_ti(hierarchy_obj)
            execute_ephemeral_ti(self.tm1_service, create_ti)
            assert self.tm1_service.hierarchies.exists(dim_name, hier_name)
        finally:
            if self.tm1_service.hierarchies.exists(dim_name, hier_name):
                self.tm1_service.hierarchies.delete(dimension_name=dim_name, hierarchy_name=hier_name)
            if self.tm1_service.dimensions.exists(dim_name):
                self.tm1_service.dimensions.delete(dim_name)

    def test_element_create_update_delete_via_ephemeral_ti(self):
        dim_name = _uid("TI_P2_EDIM")
        hier_name = dim_name
        elem_name = _uid("EL")
        source_path = f"dimensions/{dim_name}.hierarchies/{hier_name}.json/{elem_name}"
        element_numeric = Element(name=elem_name, type="Numeric", source_path=source_path)
        element_string = Element(name=elem_name, type="String", source_path=source_path)

        try:
            self.tm1_service.dimensions.create(TM1py.Dimension(dim_name))

            execute_ephemeral_ti(self.tm1_service, build_element_create_ti(element_numeric))
            assert self.tm1_service.elements.exists(dim_name, hier_name, elem_name)
            created = self.tm1_service.elements.get(dimension_name=dim_name, hierarchy_name=hier_name, element_name=elem_name)
            assert str(getattr(created, "element_type", "")).lower().startswith("numeric")

            execute_ephemeral_ti(self.tm1_service, build_element_update_ti(element_string))
            updated = self.tm1_service.elements.get(dimension_name=dim_name, hierarchy_name=hier_name, element_name=elem_name)
            assert str(getattr(updated, "element_type", "")).lower().startswith("string")

            execute_ephemeral_ti(self.tm1_service, build_element_delete_ti(element_string))
            assert not self.tm1_service.elements.exists(dim_name, hier_name, elem_name)
        finally:
            if self.tm1_service.elements.exists(dim_name, hier_name, elem_name):
                self.tm1_service.elements.delete(dim_name, hier_name, elem_name)
            if self.tm1_service.dimensions.exists(dim_name):
                self.tm1_service.dimensions.delete(dim_name)

    def test_edge_create_update_delete_via_ephemeral_ti(self):
        dim_name = _uid("TI_P2_GDIM")
        hier_name = dim_name
        parent = _uid("PARENT")
        child = _uid("CHILD")
        source_path = f"dimensions/{dim_name}.hierarchies/{hier_name}.json/{parent}:{child}"

        try:
            _create_dimension_with_default_hierarchy(self.tm1_service, dim_name)
            self.tm1_service.elements.create(dim_name, hier_name, TM1py.Element(parent, "Consolidated"))
            self.tm1_service.elements.create(dim_name, hier_name, TM1py.Element(child, "Numeric"))

            edge_w1 = Edge(parent=parent, name=child, weight=1, source_path=source_path)
            execute_ephemeral_ti(self.tm1_service, build_edge_create_ti(edge_w1))
            edges = self.tm1_service.elements.get_edges(dimension_name=dim_name, hierarchy_name=hier_name)
            assert edges.get((parent, child)) == 1

            edge_w2 = Edge(parent=parent, name=child, weight=2, source_path=source_path)
            execute_ephemeral_ti(self.tm1_service, build_edge_update_ti(edge_w2))
            edges_updated = self.tm1_service.elements.get_edges(dimension_name=dim_name, hierarchy_name=hier_name)
            assert edges_updated.get((parent, child)) == 2

            execute_ephemeral_ti(self.tm1_service, build_edge_delete_ti(edge_w2))
            edges_deleted = self.tm1_service.elements.get_edges(dimension_name=dim_name, hierarchy_name=hier_name)
            assert (parent, child) not in edges_deleted
        finally:
            if self.tm1_service.dimensions.exists(dim_name):
                self.tm1_service.dimensions.delete(dim_name)

    def test_subset_create_update_delete_via_ephemeral_ti(self):
        dim_name = _uid("TI_P2_SDIM")
        hier_name = dim_name
        subset_name = _uid("SUB")
        source_path = f"dimensions/{dim_name}.hierarchies/{hier_name}.subsets/{subset_name}.json"
        subset_create = Subset(name=subset_name, expression="{TM1SUBSETALL([" + dim_name + "])}", source_path=source_path)
        subset_update = Subset(
            name=subset_name,
            expression="{[" + dim_name + "].[" + hier_name + "].Members}",
            source_path=source_path,
        )

        try:
            _create_dimension_with_default_hierarchy(self.tm1_service, dim_name)

            execute_ephemeral_ti(self.tm1_service, build_subset_create_ti(subset_create))
            assert self.tm1_service.subsets.exists(subset_name, dim_name, hier_name)

            execute_ephemeral_ti(self.tm1_service, build_subset_update_ti(subset_update))
            updated_subset = self.tm1_service.subsets.get(subset_name, dim_name, hier_name)
            assert dim_name in (updated_subset.expression or "")

            execute_ephemeral_ti(self.tm1_service, build_subset_delete_ti(subset_update))
            assert not self.tm1_service.subsets.exists(subset_name, dim_name, hier_name)
        finally:
            if self.tm1_service.subsets.exists(subset_name, dim_name, hier_name):
                self.tm1_service.subsets.delete(subset_name=subset_name, dimension_name=dim_name, hierarchy_name=hier_name)
            if self.tm1_service.dimensions.exists(dim_name):
                self.tm1_service.dimensions.delete(dim_name)

    def test_cube_create_via_ephemeral_ti(self):
        dim_a = _uid("TI_P2_CDA")
        dim_b = _uid("TI_P2_CDB")
        cube_name = _uid("TI_P2_CUBE")
        cube_obj = Cube(
            name=cube_name,
            dimensions=[
                Dimension(name=dim_a, hierarchies=[], defaultHierarchy=None, source_path=f"dimensions/{dim_a}.json"),
                Dimension(name=dim_b, hierarchies=[], defaultHierarchy=None, source_path=f"dimensions/{dim_b}.json"),
            ],
            rules=[],
            views=[],
            source_path=f"cubes/{cube_name}.json",
        )

        try:
            self.tm1_service.dimensions.create(TM1py.Dimension(dim_a))
            self.tm1_service.dimensions.create(TM1py.Dimension(dim_b))

            execute_ephemeral_ti(self.tm1_service, build_cube_create_ti(cube_obj))
            assert self.tm1_service.cubes.exists(cube_name)
            cube_live = self.tm1_service.cubes.get(cube_name)
            assert cube_live.dimensions == [dim_a, dim_b]
        finally:
            if self.tm1_service.cubes.exists(cube_name):
                self.tm1_service.cubes.delete(cube_name)
            if self.tm1_service.dimensions.exists(dim_a):
                self.tm1_service.dimensions.delete(dim_a)
            if self.tm1_service.dimensions.exists(dim_b):
                self.tm1_service.dimensions.delete(dim_b)

    def test_mdx_view_create_update_delete_via_ephemeral_ti(self):
        cube_name = "TestCube1"
        view_name = _uid("TI_P2_MDXV")
        source_path = f"cubes/{cube_name}.views/{view_name}.json"
        mdx_1 = f"SELECT {{[TestDim1].[TestDim1].Members}} ON 0 FROM [{cube_name}]"
        mdx_2 = f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 FROM [{cube_name}]"

        view_create = MDXView(name=view_name, mdx=mdx_1, source_path=source_path)
        view_update = MDXView(name=view_name, mdx=mdx_2, source_path=source_path)

        try:
            execute_ephemeral_ti(self.tm1_service, build_mdxview_create_ti(view_create))
            assert _view_exists(self.tm1_service, cube_name, view_name)
            mdx_result = self.tm1_service.cells.execute_mdx(mdx_1)
            assert mdx_result is not None

            execute_ephemeral_ti(self.tm1_service, build_mdxview_update_ti(view_update))
            updated = self.tm1_service.views.get_mdx_view(cube_name=cube_name, view_name=view_name)
            assert "TestDim1Elem1" in updated.mdx
            mdx_result_updated = self.tm1_service.cells.execute_mdx(updated.mdx)
            assert mdx_result_updated is not None

            execute_ephemeral_ti(self.tm1_service, build_mdxview_delete_ti(view_update))
            assert not _view_exists(self.tm1_service, cube_name, view_name)
        finally:
            if _view_exists(self.tm1_service, cube_name, view_name):
                self.tm1_service.views.delete(cube_name=cube_name, view_name=view_name)

    def test_native_view_create_update_delete_via_ephemeral_ti(self):
        cube_name = "TestCube1"
        view_name = _uid("TI_P2_NATV")
        source_path = f"cubes/{cube_name}.views/{view_name}.json"
        native_view = NativeView(
            name=view_name,
            columns=[],
            rows=[],
            titles=[],
            suppress_empty_columns=True,
            suppress_empty_rows=True,
            format_string="0.#########",
            source_path=source_path,
        )

        try:
            execute_ephemeral_ti(self.tm1_service, build_native_view_create_ti(native_view))
            assert _view_exists(self.tm1_service, cube_name, view_name)

            execute_ephemeral_ti(self.tm1_service, build_native_view_update_ti(native_view))
            assert _view_exists(self.tm1_service, cube_name, view_name)

            execute_ephemeral_ti(self.tm1_service, build_native_view_delete_ti(native_view))
            assert not _view_exists(self.tm1_service, cube_name, view_name)
        finally:
            if _view_exists(self.tm1_service, cube_name, view_name):
                self.tm1_service.views.delete(cube_name=cube_name, view_name=view_name)

    def test_rule_create_and_update_via_ephemeral_ti(self):
        cube_name = _uid("TI_P2_RULE_CUBE")
        dim_a = "TestDim1"
        dim_b = "TestDim2"

        cube_rule_1 = Cube(
            name=cube_name,
            dimensions=[],
            rules=[Rule(area="[default]", full_statement="SKIPCHECK;\n['TestDim1Elem1'] = N: 1;")],
            views=[],
            source_path=f"cubes/{cube_name}.json",
        )
        cube_rule_2 = Cube(
            name=cube_name,
            dimensions=[],
            rules=[Rule(area="[default]", full_statement="SKIPCHECK;\n['TestDim1Elem1'] = N: 2;")],
            views=[],
            source_path=f"cubes/{cube_name}.json",
        )

        try:
            self.tm1_service.cubes.create(TM1py.Cube(name=cube_name, dimensions=[dim_a, dim_b], rules=""))

            execute_ephemeral_ti(self.tm1_service, build_cube_update_ti(cube_rule_1))
            cube_after_create = self.tm1_service.cubes.get(cube_name)
            assert cube_after_create.rules is not None
            assert " = N: 1;" in str(cube_after_create.rules)

            execute_ephemeral_ti(self.tm1_service, build_cube_update_ti(cube_rule_2))
            cube_after_update = self.tm1_service.cubes.get(cube_name)
            assert cube_after_update.rules is not None
            assert " = N: 2;" in str(cube_after_update.rules)
        finally:
            if self.tm1_service.cubes.exists(cube_name):
                self.tm1_service.cubes.delete(cube_name)

    def test_cube_delete_via_ephemeral_ti(self):
        cube_name = _uid("TI_P2_CUBE_DEL")
        cube_obj = Cube(name=cube_name, dimensions=[], rules=[], views=[], source_path=f"cubes/{cube_name}.json")

        try:
            self.tm1_service.cubes.create(TM1py.Cube(name=cube_name, dimensions=["TestDim1", "TestDim2"], rules=""))
            assert self.tm1_service.cubes.exists(cube_name)

            execute_ephemeral_ti(self.tm1_service, build_cube_delete_ti(cube_obj))
            assert not self.tm1_service.cubes.exists(cube_name)
        finally:
            if self.tm1_service.cubes.exists(cube_name):
                self.tm1_service.cubes.delete(cube_name)
