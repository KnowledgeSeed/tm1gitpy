import uuid

import pytest
import TM1py
from TM1py import TM1Service

from test_integration.test_base import execute_ephemeral_ti, tm1_service
from tm1_git_py.apply import build_master_changeset_ti
from tm1_git_py.changeset import Change, ChangeType, Changeset, ObjectType
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.element import Element, build_element_update_ti
from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.subset import Subset


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


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


class TestTIMasterOrchestrator:

    def test_dependency_sorting_dimension_hierarchy_cube_view(self):
        dim_name = "TI_P3_DIM"
        hier_name = "TI_P3_HIER"
        cube_name = "TI_P3_CUBE"
        view_name = "TI_P3_VIEW"

        dimension_obj = Dimension(
            name=dim_name,
            hierarchies=[],
            defaultHierarchy=None,
            source_path=f"dimensions/{dim_name}.json",
        )
        hierarchy_obj = Hierarchy(
            name=hier_name,
            elements=[],
            edges=[],
            subsets=[],
            source_path=f"dimensions/{dim_name}.hierarchies/{hier_name}.json",
        )
        cube_obj = Cube(
            name=cube_name,
            dimensions=[dimension_obj],
            rules=[],
            views=[],
            source_path=f"cubes/{cube_name}.json",
        )
        view_obj = MDXView(
            name=view_name,
            mdx=f"SELECT {{}} ON 0 FROM [{cube_name}]",
            source_path=f"cubes/{cube_name}.views/{view_name}.json",
        )

        changeset = Changeset("ti_p3_dependency_sort")
        changeset.changes = [
            Change(ChangeType.ADD, ObjectType.MDX_VIEW, view_obj.source_path, view_obj),
            Change(ChangeType.ADD, ObjectType.CUBE, cube_obj.source_path, cube_obj),
            Change(ChangeType.ADD, ObjectType.DIMENSION, dimension_obj.source_path, dimension_obj),
            Change(ChangeType.ADD, ObjectType.HIERARCHY, hierarchy_obj.source_path, hierarchy_obj),
        ]

        master_ti = build_master_changeset_ti(changeset)

        dim_pos = master_ti.index(f"# --- Create Dimension: {dim_name} ---")
        hier_pos = master_ti.index(f"# --- Create Hierarchy: {dim_name}:{hier_name} ---")
        cube_pos = master_ti.index(f"# --- Create Cube: {cube_name} ---")
        view_pos = master_ti.index(f"# --- Create MDX View: {view_name} in Cube: {cube_name} ---")

        assert dim_pos < hier_pos < cube_pos < view_pos

    def test_batch_compilation_contains_all_expected_snippets(self):
        dim_name = "TI_P3B_DIM_NEW"
        subset_name = "TI_P3B_SUB_NEW"
        cube_name_old = "TI_P3B_CUBE_OLD"
        view_name_old = "TI_P3B_VIEW_OLD"

        dimension_obj = Dimension(
            name=dim_name,
            hierarchies=[],
            defaultHierarchy=None,
            source_path=f"dimensions/{dim_name}.json",
        )
        subset_obj = Subset(
            name=subset_name,
            expression="{TM1SUBSETALL([TI_P3B_DIM_NEW])}",
            source_path=f"dimensions/{dim_name}.hierarchies/{dim_name}.subsets/{subset_name}.json",
        )
        view_obj_remove = MDXView(
            name=view_name_old,
            mdx="",
            source_path=f"cubes/{cube_name_old}.views/{view_name_old}.json",
        )
        cube_obj_remove = Cube(
            name=cube_name_old,
            dimensions=[],
            rules=[],
            views=[],
            source_path=f"cubes/{cube_name_old}.json",
        )

        changeset = Changeset("ti_p3_batch_compile")
        changeset.changes = [
            Change(ChangeType.ADD, ObjectType.DIMENSION, dimension_obj.source_path, dimension_obj),
            Change(ChangeType.ADD, ObjectType.SUBSET, subset_obj.source_path, subset_obj),
            Change(ChangeType.REMOVE, ObjectType.MDX_VIEW, view_obj_remove.source_path, view_obj_remove),
            Change(ChangeType.REMOVE, ObjectType.CUBE, cube_obj_remove.source_path, cube_obj_remove),
        ]

        master_ti = build_master_changeset_ti(changeset)

        expected_headers = [
            f"# --- Delete MDX View: {view_name_old} in Cube: {cube_name_old} ---",
            f"# --- Delete Cube: {cube_name_old} ---",
            f"# --- Create Dimension: {dim_name} ---",
            f"# --- Create Subset: {subset_name} in {dim_name} ---",
        ]

        for header in expected_headers:
            assert header in master_ti

        # Snippets are appended as blocks and separated by empty lines.
        assert "\r\n\r\n# ---" in master_ti

    def test_master_ti_executes_mixed_changeset_operations(self, tm1_service):
        cube_name = "TestCube3WithView"
        rule_cube_name = "TestCube2WithRule"
        view_modify_name = "testcube3withview_view1"
        view_remove_name = _uid("TI_P3_REMOVE_VIEW")
        new_dim_name = _uid("TI_P3_DIM_EXEC")
        new_subset_name = _uid("TI_P3_SUB_EXEC")

        view_modify_path = f"cubes/{cube_name}.views/{view_modify_name}.json"
        view_remove_path = f"cubes/{cube_name}.views/{view_remove_name}.json"
        dim_path = f"dimensions/{new_dim_name}.json"
        subset_path = f"dimensions/TestDim1.hierarchies/TestDim1.subsets/{new_subset_name}.json"
        rule_cube_path = f"cubes/{rule_cube_name}.json"

        view_before = tm1_service.views.get_mdx_view(cube_name=cube_name, view_name=view_modify_name)
        old_rule_body = str(getattr(tm1_service.cubes.get(rule_cube_name).rules, "body", "") or "")

        temp_remove_view = TM1py.MDXView(
            cube_name=cube_name,
            view_name=view_remove_name,
            MDX=f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 FROM [{cube_name}]",
        )
        tm1_service.views.create(temp_remove_view)

        changeset = Changeset("ti_p3_exec_mixed_changeset")
        changeset.changes = [
            Change(
                ChangeType.REMOVE,
                ObjectType.MDX_VIEW,
                view_remove_path,
                MDXView(name=view_remove_name, mdx="", source_path=view_remove_path),
            ),
            Change(
                ChangeType.MODIFY,
                ObjectType.MDX_VIEW,
                view_modify_path,
                MDXView(
                    name=view_modify_name,
                    mdx=f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 FROM [{cube_name}]",
                    source_path=view_modify_path,
                ),
            ),
            Change(
                ChangeType.ADD,
                ObjectType.DIMENSION,
                dim_path,
                Dimension(name=new_dim_name, hierarchies=[], defaultHierarchy=None, source_path=dim_path),
            ),
            Change(
                ChangeType.ADD,
                ObjectType.SUBSET,
                subset_path,
                Subset(
                    name=new_subset_name,
                    expression="{[TestDim1].[TestDim1].Members}",
                    source_path=subset_path,
                ),
            ),
            Change(
                ChangeType.MODIFY,
                ObjectType.CUBE,
                rule_cube_path,
                Cube(
                    name=rule_cube_name,
                    dimensions=[],
                    rules=[Rule(area="[default]", full_statement="SKIPCHECK;\n['TestDim1Elem1'] = N: 2;")],
                    views=[],
                    source_path=rule_cube_path,
                ),
            ),
        ]

        master_ti = build_master_changeset_ti(changeset)

        try:
            execute_ephemeral_ti(tm1_service, master_ti)

            view_exists_after = tm1_service.views.exists(cube_name=cube_name, view_name=view_remove_name)
            if isinstance(view_exists_after, (list, tuple)):
                assert not any(view_exists_after)
            else:
                assert not view_exists_after

            modified_view = tm1_service.views.get_mdx_view(cube_name=cube_name, view_name=view_modify_name)
            assert "TestDim1Elem1" in modified_view.mdx

            assert tm1_service.dimensions.exists(new_dim_name)
            assert tm1_service.subsets.exists(new_subset_name, "TestDim1", "TestDim1")

            updated_rule_cube = tm1_service.cubes.get(rule_cube_name)
            assert updated_rule_cube.rules is not None
            assert " = N: 2;" in str(updated_rule_cube.rules)
        finally:
            if tm1_service.dimensions.exists(new_dim_name):
                tm1_service.dimensions.delete(new_dim_name)
            if tm1_service.subsets.exists(new_subset_name, "TestDim1", "TestDim1"):
                tm1_service.subsets.delete(
                    subset_name=new_subset_name,
                    dimension_name="TestDim1",
                    hierarchy_name="TestDim1",
                )

            try:
                tm1_service.views.delete(cube_name=cube_name, view_name=view_remove_name)
            except Exception:
                pass
            try:
                tm1_service.views.update(view_before)
            except Exception:
                pass

            cube_restore = tm1_service.cubes.get(rule_cube_name)
            cube_restore.rules = TM1py.Rules(old_rule_body)
            tm1_service.cubes.update(cube_restore)


@pytest.mark.usefixtures("tm1_service")
class TestTIAtomicity:

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service: TM1Service = tm1_service

    def test_fail_fast_rollback_dimension_is_not_persisted(self):
        dim_valid = _uid("Atomicity_Test_Dim")
        cube_invalid = _uid("Atomicity_Test_Cube")
        missing_dim = _uid("Non_Existent_Dim")

        valid_dim_obj = Dimension(
            name=dim_valid,
            hierarchies=[],
            defaultHierarchy=None,
            source_path=f"dimensions/{dim_valid}.json",
        )
        invalid_cube_obj = Cube(
            name=cube_invalid,
            dimensions=[
                Dimension(name=dim_valid, hierarchies=[], defaultHierarchy=None, source_path=f"dimensions/{dim_valid}.json"),
                Dimension(name=missing_dim, hierarchies=[], defaultHierarchy=None, source_path=f"dimensions/{missing_dim}.json"),
            ],
            rules=[],
            views=[],
            source_path=f"cubes/{cube_invalid}.json",
        )

        changeset = Changeset("ti_p4_fail_fast_rollback")
        changeset.changes = [
            Change(ChangeType.ADD, ObjectType.DIMENSION, valid_dim_obj.source_path, valid_dim_obj),
            Change(ChangeType.ADD, ObjectType.CUBE, invalid_cube_obj.source_path, invalid_cube_obj),
        ]
        master_ti = build_master_changeset_ti(changeset)

        with pytest.raises(Exception):
            execute_ephemeral_ti(self.tm1_service, master_ti)

        # Atomicity expectation: valid dimension creation is rolled back.
        assert not self.tm1_service.dimensions.exists(dim_valid)

        # Defensive cleanup if rollback failed.
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
        measure_as_string = Element(name=measure_el, type="String", source_path=source_path)

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

            update_ti = build_element_update_ti(measure_as_string)
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
