import re
import uuid

import pytest
import TM1py
from TM1py import TM1Service

from test_integration.test_base import (
    execute_ephemeral_ti,
    tm1_service,
    export_check_no_errors,
    load_fixture_model_tm1gitpy,
)
from tm1_git_py.services.apply import build_master_changeset_ti
from tm1_git_py.services.changeset import Change, ChangeType, Changeset, ObjectType
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.element import Element, build_element_update_ti
from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.subset import Subset

import tm1_git_py.model.element as element_mod
import tm1_git_py.model.hierarchy as hierarchy_mod
import tm1_git_py.model.mdxview as mdxview_mod
import tm1_git_py.model.subset as subset_mod
from tm1_git_py.services.comparator import Comparator
from tm1_git_py.services.filter import FilterRules, DEFAULT_TM1_TECHNICAL_OBJECTS


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


def _patch_builder_compat(monkeypatch):
    orig_build_hierarchy_create_ti = hierarchy_mod.build_hierarchy_create_ti
    orig_build_mdxview_create_ti = mdxview_mod.build_mdxview_create_ti
    orig_build_mdxview_update_ti = mdxview_mod.build_mdxview_update_ti
    orig_build_mdxview_delete_ti = mdxview_mod.build_mdxview_delete_ti
    orig_build_subset_create_ti = subset_mod.build_subset_create_ti
    orig_build_subset_update_ti = subset_mod.build_subset_update_ti
    orig_build_subset_delete_ti = subset_mod.build_subset_delete_ti

    monkeypatch.setattr(
        hierarchy_mod,
        "build_hierarchy_create_ti",
        lambda hierarchy, dimension_name=None, uri=None: orig_build_hierarchy_create_ti(
            hierarchy,
            dimension_name=dimension_name,
            uri=uri or _uri_from_source_path(hierarchy),
        ),
    )
    monkeypatch.setattr(
        mdxview_mod,
        "build_mdxview_create_ti",
        lambda mdx_view, uri=None: orig_build_mdxview_create_ti(
            mdx_view,
            uri=uri or _uri_from_source_path(mdx_view),
        ),
    )
    monkeypatch.setattr(
        mdxview_mod,
        "build_mdxview_update_ti",
        lambda mdx_view, uri=None: orig_build_mdxview_update_ti(
            mdx_view,
            uri=uri or _uri_from_source_path(mdx_view),
        ),
    )
    monkeypatch.setattr(
        mdxview_mod,
        "build_mdxview_delete_ti",
        lambda mdx_view, uri=None: orig_build_mdxview_delete_ti(
            mdx_view,
            uri=uri or _uri_from_source_path(mdx_view),
        ),
    )
    monkeypatch.setattr(
        subset_mod,
        "build_subset_create_ti",
        lambda subset, uri=None: orig_build_subset_create_ti(
            subset,
            uri=uri or _uri_from_source_path(subset),
        ),
    )
    monkeypatch.setattr(
        subset_mod,
        "build_subset_update_ti",
        lambda subset, uri=None: orig_build_subset_update_ti(
            subset,
            uri=uri or _uri_from_source_path(subset),
        ),
    )
    monkeypatch.setattr(
        subset_mod,
        "build_subset_delete_ti",
        lambda subset, uri=None: orig_build_subset_delete_ti(
            subset,
            uri=uri or _uri_from_source_path(subset),
        ),
    )


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
class TestTIMasterOrchestrator:

    _fixture_model_id_no_meta = "fixture_model_no_meta"
    _fixture_model_id_with_meta = "fixture_model_with_meta"
    _f_no_meta = DEFAULT_TM1_TECHNICAL_OBJECTS
    _f_with_meta = [
        "!Cubes('}*')",
        "!Dimensions('}*')",
        "!Processes('}*')",
    ]
    
    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service: TM1Service = tm1_service

    @staticmethod
    def _changes_by(changeset: Changeset, change_type: ChangeType, class_name: str):
        return [
            change.body
            for change in changeset.changes
            if change.change_type == change_type
            and change.body.__class__.__name__ == class_name
        ]
    
    def test_dependency_sorting_dimension_hierarchy_cube_view(self, monkeypatch):
        _patch_builder_compat(monkeypatch)

        dim_name = "TI_P3_DIM"
        hier_name = "TI_P3_HIER"
        cube_name = "TI_P3_CUBE"
        view_name = "TI_P3_VIEW"

        dimension_obj = _set_source_path(
            Dimension(name=dim_name, hierarchies=[], defaultHierarchy=None),
            f"dimensions/{dim_name}.json",
        )
        hierarchy_obj = _set_source_path(
            Hierarchy(name=hier_name, elements=[], edges=[], subsets=[]),
            f"dimensions/{dim_name}.hierarchies/{hier_name}.json",
        )
        cube_obj = _set_source_path(
            Cube(name=cube_name, dimensions=[dim_name], rules=[], views=[]),
            f"cubes/{cube_name}.json",
        )
        view_obj = _set_source_path(
            MDXView(name=view_name, mdx=f"SELECT {{}} ON 0 FROM [{cube_name}]"),
            f"cubes/{cube_name}.views/{view_name}.json",
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

    def test_batch_compilation_contains_all_expected_snippets(self, monkeypatch):
        _patch_builder_compat(monkeypatch)

        dim_name = "TI_P3B_DIM_NEW"
        subset_name = "TI_P3B_SUB_NEW"
        cube_name_old = "TI_P3B_CUBE_OLD"
        view_name_old = "TI_P3B_VIEW_OLD"

        dimension_obj = _set_source_path(
            Dimension(name=dim_name, hierarchies=[], defaultHierarchy=None),
            f"dimensions/{dim_name}.json",
        )
        subset_obj = _set_source_path(
            Subset(name=subset_name, expression=f"{{TM1SUBSETALL([{dim_name}])}}"),
            f"dimensions/{dim_name}.hierarchies/{dim_name}.subsets/{subset_name}.json",
        )
        view_obj_remove = _set_source_path(
            MDXView(name=view_name_old, mdx=""),
            f"cubes/{cube_name_old}.views/{view_name_old}.json",
        )
        cube_obj_remove = _set_source_path(
            Cube(name=cube_name_old, dimensions=[], rules=[], views=[]),
            f"cubes/{cube_name_old}.json",
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

        assert "\r\n\r\n# ---" in master_ti

    def test_master_ti_executes_mixed_changeset_operations(self):
        temp_hierarchy_name = "TmpHierForChangeset"
        process_name = "myprocess2"
        cube_name = "TestCube3WithView"
        view_name = "testcube3withview_view1"
        native_view_name = "TestCube3WithView_view2"
        rule_cube_name = "TestCube2WithRule"
        extra_element_name = "zz_mixed_element"

        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, model_id=self._fixture_model_id_no_meta
        )
        fixture_cube = next(
            cube for cube in fixture_model.cubes if cube.name == cube_name
        )
        fixture_mdx_view = next(
            view
            for view in fixture_cube.views
            if isinstance(view, MDXView) and view.name == view_name
        )

        # Preconditions for deterministic behaviour.
        if process_name in self.tm1_service.processes.get_all_names(
            skip_control_processes=False
        ):
            self.tm1_service.processes.delete(process_name)
        try:
            self.tm1_service.elements.delete(
                "TestDimMultiHier", "TestDimMultiHier", extra_element_name
            )
        except Exception:
            pass
        try:
            self.tm1_service.elements.remove_edge(
                "TestDimMultiHier", "TestDimMultiHier", "DimElemC", "DimElem1"
            )
        except Exception:
            pass
        try:
            self.tm1_service.hierarchies.delete(
                dimension_name="TestDim1", hierarchy_name=temp_hierarchy_name
            )
        except Exception:
            pass
        try:
            self.tm1_service.views.delete(
                cube_name=cube_name, view_name=native_view_name
            )
        except Exception:
            pass

        self.tm1_service.elements.create(
            hierarchy_name="TestDimMultiHier",
            dimension_name="TestDimMultiHier",
            element=TM1py.Element(name=extra_element_name, element_type="Numeric"),
        )
        self.tm1_service.elements.add_edges(
            "TestDimMultiHier", "TestDimMultiHier", {("DimElemC", "DimElem1"): 1}
        )
        self.tm1_service.hierarchies.create(
            TM1py.Hierarchy(dimension_name="TestDim1", name=temp_hierarchy_name)
        )

        mdx_view = self.tm1_service.views.get_mdx_view(
            cube_name=cube_name, view_name=view_name
        )
        mdx_view.mdx = (
            f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 "
            f"FROM [{cube_name}]"
        )
        self.tm1_service.views.update(mdx_view)

        cube = self.tm1_service.cubes.get(rule_cube_name)
        cube.rules = TM1py.Rules("SKIPCHECK;")
        self.tm1_service.cubes.update(cube)

        test_model = export_check_no_errors(self)
        changeset = self.compare(
            test_model, fixture_model, filter_rules=self._f_no_meta
        )

        removed_elements = self._changes_by(changeset, ChangeType.REMOVE, "Element")
        removed_edges = self._changes_by(changeset, ChangeType.REMOVE, "Edge")
        removed_hierarchies = self._changes_by(
            changeset, ChangeType.REMOVE, "Hierarchy"
        )
        modified_mdx_views = self._changes_by(changeset, ChangeType.MODIFY, "MDXView")
        modified_rules = self._changes_by(changeset, ChangeType.MODIFY, "Rule")
        added_native_views = self._changes_by(changeset, ChangeType.ADD, "NativeView")
        added_processes = self._changes_by(changeset, ChangeType.ADD, "Process")

        assert any(element.name == extra_element_name for element in removed_elements)
        assert any(
            edge.parent == "DimElemC" and edge.component_name == "DimElem1"
            for edge in removed_edges
        )
        assert any(
            hierarchy.name == temp_hierarchy_name for hierarchy in removed_hierarchies
        )
        assert any(view.name == view_name for view in modified_mdx_views)
        assert any(rule.name == "default" for rule in modified_rules)
        assert any(view.name == native_view_name for view in added_native_views)
        assert any(process.name == process_name for process in added_processes)

        self.apply_atomic(changeset)
        test_model = export_check_no_errors(
            self, self._f_with_meta, model_id=self._fixture_model_id_with_meta
        )

        default_hierarchy = self.tm1_service.hierarchies.get(
            "TestDimMultiHier", "TestDimMultiHier"
        )
        assert extra_element_name not in default_hierarchy.elements
        assert ("DimElemC", "DimElem1") not in default_hierarchy.edges

        testdim1 = self.tm1_service.dimensions.get("TestDim1")
        assert temp_hierarchy_name not in [
            hier.name for hier in testdim1.hierarchies
        ]

        updated_view = self.tm1_service.views.get_mdx_view(
            cube_name=cube_name, view_name=view_name
        )
        assert updated_view.mdx == fixture_mdx_view.mdx

        updated_cube = self.tm1_service.cubes.get(rule_cube_name)
        assert updated_cube.rules is not None
        assert " = 1;" in str(updated_cube.rules)

        created_native_view = self.tm1_service.views.get_native_view(
            cube_name=cube_name, view_name=native_view_name
        )
        assert created_native_view is not None

        assert process_name in self.tm1_service.processes.get_all_names(
            skip_control_processes=False
        )

    def compare(
        self, source, target, mode: str = "full", filter_rules: list[str] = None
    ):
        comparator = Comparator()
        rules = FilterRules(filter_rules) if filter_rules is not None else None
        return comparator.compare(source, target, mode=mode, filter_rules=rules)
    
    def apply_atomic(self, changeset: Changeset):
        status_dir = "test_integration"
        exec_id = "test_create_and_delete"
        success, _errors = changeset.apply_atomic(
            tm1_service=self.tm1_service, status_dir=status_dir, execution_id=exec_id
        )
        assert success, f"Changeset application failed with errors: {_errors}"
        

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
