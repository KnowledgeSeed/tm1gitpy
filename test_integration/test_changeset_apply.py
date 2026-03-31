import tempfile
from pathlib import Path

import pytest
import TM1py
from TM1py import Cube, Dimension, Hierarchy, TM1Service

from test_integration.test_base import (
    assert_export_matches_expected_subdirs,
    export_check_no_errors,
    load_fixture_model_tm1gitpy,
    tm1_service,
)
from tm1_git_py.changeset import ChangeType, Changeset, Change, ObjectType
from tm1_git_py.comparator import Comparator
from tm1_git_py.model.edge import Edge
from tm1_git_py.model.element import Element
from tm1_git_py.model.hierarchy import Hierarchy as GitHierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.model.process import Process as GitProcess
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.subset import Subset as GitSubset
from tm1_git_py.model.ti import TI
from tm1_git_py.serializer import serialize_model

@pytest.mark.usefixtures("tm1_service")
class TestChangesetApply:

    _f_no_meta_obj = ["Cubes('}*')", "Dimensions('}*')"]

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service : TM1Service = tm1_service

    @staticmethod
    def _changes_by(changeset: Changeset, change_type: ChangeType, class_name: str):
        return [
            change.body for change in changeset.changes
            if change.change_type == change_type and change.body.__class__.__name__ == class_name
        ]

    def _restore_fixture_no_meta(self, fixture_dir: str, fixture_model):
        current_model = export_check_no_errors(self, self._f_no_meta_obj)
        restore_changeset = self.compare(current_model, fixture_model)
        if restore_changeset.has_changes():
            self.apply(restore_changeset)
        restored_model = export_check_no_errors(self, self._f_no_meta_obj)
        self.check_no_diff(fixture_dir, restored_model)

    def test_create_cube_full_no_meta_objects(self):
        
        # given
        fixture_model, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)

        self.tm1_service.cubes.delete("TestCube1")
        test_model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(test_model, fixture_tm1gitpy_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_no_meta_obj)

        # then
        added_cubes = self._changes_by(changeset, ChangeType.ADD, "Cube")
        assert len(added_cubes) == 1
        assert added_cubes[0].name == "TestCube1"
        self.check_no_diff(fixture_model, test_model)

    def test_create_cube_full_with_meta_objects(self):
        
        # given
        fixture_model, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, [])

        self.tm1_service.cubes.delete("TestCube1")
        test_model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(test_model, fixture_tm1gitpy_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, [])

        # then
        added_cubes = self._changes_by(changeset, ChangeType.ADD, "Cube")
        assert len(added_cubes) == 1
        assert added_cubes[0].name == "TestCube1"
        self.check_no_diff(fixture_model, test_model)

    def test_create_cube_add_only_no_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)

        self.tm1_service.cubes.delete("TestCube1")
        test_model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(test_model, fixture_tm1gitpy_model, mode='add_only')
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_no_meta_obj)

        # then
        added_cubes = self._changes_by(changeset, ChangeType.ADD, "Cube")
        assert len(added_cubes) == 1
        assert added_cubes[0].name == "TestCube1"
        self.check_no_diff(fixture_tm1gitpy_dir, test_model)

    def test_create_cube_add_only_with_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, [])

        self.tm1_service.cubes.delete("TestCube1")
        test_model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(test_model, fixture_tm1gitpy_model, mode='add_only')
        self.apply(changeset)
        test_model = export_check_no_errors(self, [])

        # then
        added_cubes = self._changes_by(changeset, ChangeType.ADD, "Cube")
        assert len(added_cubes) == 1
        assert added_cubes[0].name == "TestCube1"
        self.check_no_diff(fixture_tm1gitpy_dir, test_model)

    def test_delete_cube_full_no_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        
        self.tm1_service.cubes.create(Cube("TestCubeRemovable1", dimensions=["TestDim1", "TestDim2"]))
        test_model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(test_model, fixture_tm1gitpy_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_no_meta_obj)

        # then
        removed_cubes = self._changes_by(changeset, ChangeType.REMOVE, "Cube")
        assert len(removed_cubes) == 1
        assert removed_cubes[0].name == "TestCubeRemovable1"
        self.check_no_diff(fixture_tm1gitpy_dir, test_model)

    def test_delete_cube_full_with_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, [])
        
        self.tm1_service.cubes.create(Cube("TestCubeRemovable2", dimensions=["TestDim1", "TestDim2"]))
        test_model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(test_model, fixture_tm1gitpy_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, [])

        # then
        removed_cubes = self._changes_by(changeset, ChangeType.REMOVE, "Cube")
        assert len(removed_cubes) == 1
        assert removed_cubes[0].name == "TestCubeRemovable2"
        self.check_no_diff(fixture_tm1gitpy_dir, test_model)

    def test_delete_cube_add_only_no_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        
        self.tm1_service.cubes.create(Cube("TestCubeRemovable3", dimensions=["TestDim1", "TestDim2"]))
        test_model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(test_model, fixture_tm1gitpy_model, mode='add_only')
        self.apply(changeset)
        
        # then
        assert not self._changes_by(changeset, ChangeType.REMOVE, "Cube")
        self.check_no_diff(fixture_tm1gitpy_dir, test_model)

    def test_delete_cube_add_only_with_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self)
        
        self.tm1_service.cubes.create(Cube("TestCubeRemovable4", dimensions=["TestDim1", "TestDim2"]))
        test_model = export_check_no_errors(self)
        
        # when
        changeset = self.compare(test_model, fixture_tm1gitpy_model, mode='add_only')
        self.apply(changeset)
        
        # then
        assert not self._changes_by(changeset, ChangeType.REMOVE, "Cube")
        self.check_no_diff(fixture_tm1gitpy_dir, test_model)


    # -----------------------------------------------------------------------
    # View tests
    # -----------------------------------------------------------------------

    def test_apply_add_mdxview(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        cube_name = "TestCube3WithView"
        view_name = "zz_temp_mdx_view_add"
        source_path = f"cubes/{cube_name}.views/{view_name}.json"
        try:
            self.tm1_service.views.delete(cube_name=cube_name, view_name=view_name)
        except Exception:
            pass

        changeset = Changeset("add_mdxview_case")
        changeset.changes = [Change(
            change_type=ChangeType.ADD,
            object_type=ObjectType.MDX_VIEW,
            source_path=source_path,
            body=MDXView(name=view_name,
                         mdx=f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 FROM [{cube_name}]")
        )]
        try:
            self.apply(changeset)
            assert self.tm1_service.views.exists(cube_name=cube_name, view_name=view_name)
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)

    def test_apply_remove_mdxview(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        cube_name = "TestCube3WithView"
        view_name = "zz_temp_mdx_view_remove"
        source_path = f"cubes/{cube_name}.views/{view_name}.json"
        self.tm1_service.views.create(TM1py.MDXView(cube_name=cube_name, view_name=view_name,
                                                    MDX=f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 FROM [{cube_name}]"))

        changeset = Changeset("remove_mdxview_case")
        changeset.changes = [Change(
            change_type=ChangeType.REMOVE,
            object_type=ObjectType.MDX_VIEW,
            source_path=source_path,
            body=MDXView(name=view_name, mdx="")
        )]
        try:
            self.apply(changeset)
            exists_private_public = self.tm1_service.views.exists(cube_name=cube_name, view_name=view_name)
            assert not any(exists_private_public)
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)

    def test_apply_remove_nativeview(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        cube_name = "TestCube3WithView"
        view_name = "zz_temp_native_view_remove"
        source_path = f"cubes/{cube_name}.views/{view_name}.json"
        nv = TM1py.NativeView.from_dict(
            view_as_dict={
                "Name": view_name,
                "Columns": [{"Subset": {"Expression": "{[TestDim2].[TestDim2].Members}",
                                        "Hierarchy@odata.bind": "Dimensions('TestDim2')/Hierarchies('TestDim2')"}}],
                "Rows": [{"Subset": {"Expression": "{[TestDim1].[TestDim1].Members}",
                                     "Hierarchy@odata.bind": "Dimensions('TestDim1')/Hierarchies('TestDim1')"}}],
                "Titles": [],
                "SuppressEmptyColumns": True,
                "SuppressEmptyRows": True,
                "FormatString": "0.#########",
            },
            cube_name=cube_name
        )
        self.tm1_service.views.create(nv)

        changeset = Changeset("remove_nativeview_case")
        changeset.changes = [Change(
            change_type=ChangeType.REMOVE,
            object_type=ObjectType.NATIVE_VIEW,
            source_path=source_path,
            body=NativeView(
                name=view_name,
                columns=[],
                rows=[],
                titles=[],
                suppress_empty_columns=True,
                suppress_empty_rows=True,
                format_string="0.#########",
            )
        )]
        try:
            self.apply(changeset)
            exists_private_public = self.tm1_service.views.exists(cube_name=cube_name, view_name=view_name)
            assert not any(exists_private_public)
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)

    def test_apply_modify_nativeview(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        cube_name = "TestCube3WithView"
        view_name = "TestCube3WithView_view2"
        source_path = f"cubes/{cube_name}.views/{view_name}.json"

        changeset = Changeset("modify_nativeview_case")
        changeset.changes = [Change(
            change_type=ChangeType.MODIFY,
            object_type=ObjectType.NATIVE_VIEW,
            source_path=source_path,
            body=NativeView(
                name=view_name,
                columns=[{
                    "Subset": {"Expression": "{[TestDim2].[TestDim2].Members}",
                               "Hierarchy": {"@id": "Dimensions('TestDim2')/Hierarchies('TestDim2')"}}
                }],
                rows=[{
                    "Subset": {"Expression": "{[TestDim1].[TestDim1].[TestDim1Elem1]}",
                               "Hierarchy": {"@id": "Dimensions('TestDim1')/Hierarchies('TestDim1')"}}
                }],
                titles=[],
                suppress_empty_columns=True,
                suppress_empty_rows=False,
                format_string="0.#########",
            )
        )]
        try:
            self.apply(changeset)
            updated = self.tm1_service.views.get_native_view(cube_name=cube_name, view_name=view_name)
            assert updated.suppress_empty_rows is False
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)

    def test_compare_tracks_added_mdx_and_native_views(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        cube_name = "TestCube3WithView"
        mdx_name = "zz_temp_mdx_view_compare"
        native_name = "zz_temp_native_view_compare"
        try:
            self.tm1_service.views.delete(cube_name=cube_name, view_name=mdx_name)
        except Exception:
            pass
        try:
            self.tm1_service.views.delete(cube_name=cube_name, view_name=native_name)
        except Exception:
            pass

        self.tm1_service.views.create(
            TM1py.MDXView(
                cube_name=cube_name,
                view_name=mdx_name,
                MDX=f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 FROM [{cube_name}]"
            )
        )
        native_view = TM1py.NativeView.from_dict(
            view_as_dict={
                "Name": native_name,
                "Columns": [{"Subset": {"Expression": "{[TestDim2].[TestDim2].Members}",
                                        "Hierarchy@odata.bind": "Dimensions('TestDim2')/Hierarchies('TestDim2')"}}],
                "Rows": [{"Subset": {"Expression": "{[TestDim1].[TestDim1].Members}",
                                     "Hierarchy@odata.bind": "Dimensions('TestDim1')/Hierarchies('TestDim1')"}}],
                "Titles": [],
                "SuppressEmptyColumns": True,
                "SuppressEmptyRows": True,
                "FormatString": "0.#########",
            },
            cube_name=cube_name,
        )
        self.tm1_service.views.create(native_view)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        try:
            changeset = self.compare(model, fixture_model)
            removed_mdx = self._changes_by(changeset, ChangeType.REMOVE, "MDXView")
            removed_native = self._changes_by(changeset, ChangeType.REMOVE, "NativeView")

            assert any(v.name == mdx_name for v in removed_mdx)
            assert any(v.name == native_name for v in removed_native)
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)


    # -----------------------------------------------------------------------
    # Dimension tests
    # -----------------------------------------------------------------------

    def test_create_dimension_no_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        test_model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(test_model, fixture_tm1gitpy_model)
        self.apply(changeset)

        self.check_no_diff(fixture_tm1gitpy_dir, test_model)

    def test_create_dimension_with_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, [])

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(model, fixture_tm1gitpy_model)
        self.apply(changeset)

        self.check_no_diff(fixture_tm1gitpy_dir, model)

    def test_delete_dimension_no_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(model, fixture_tm1gitpy_model)
        self.apply(changeset)

        removed_dimensions = self._changes_by(changeset, ChangeType.REMOVE, "Dimension")
        assert len(removed_dimensions) == 1
        assert removed_dimensions[0].name == "TestDimension"
        self.check_no_diff(fixture_tm1gitpy_dir, model)

    def test_delete_dimension_with_meta_objects(self):
        
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, [])

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(model, fixture_tm1gitpy_model)
        self.apply(changeset)

        self.check_no_diff(fixture_tm1gitpy_dir, model)


    # -----------------------------------------------------------------------
    # Element and Edge tests
    # -----------------------------------------------------------------------

    def test_apply_add_element(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        element_name = "zz_temp_element_add"
        source_path = f"dimensions/TestDim1.hierarchies/TestDim1.json/{element_name}"
        try:
            self.tm1_service.elements.delete(hierarchy_name="TestDim1", dimension_name="TestDim1", element_name=element_name)
        except Exception:
            pass

        changeset = Changeset("add_element_case")
        changeset.changes = [Change(
            change_type=ChangeType.ADD,
            object_type=ObjectType.ELEMENT,
            source_path=source_path,
            body=Element(name=element_name, type="Numeric")
        )]
        try:
            self.apply(changeset)
            added = self.tm1_service.elements.get(dimension_name="TestDim1", hierarchy_name="TestDim1", element_name=element_name)
            assert added is not None
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)

    def test_apply_remove_edge(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        edge_source = "dimensions/TestDimMultiHier.hierarchies/TestDimMultiHier.json/DimElemC:a"
        try:
            self.tm1_service.elements.add_edges("TestDimMultiHier", "TestDimMultiHier", {("DimElemC", "a"): 1})
        except Exception:
            pass

        changeset = Changeset("remove_edge_case")
        changeset.changes = [Change(
            change_type=ChangeType.REMOVE,
            object_type=ObjectType.EDGE,
            source_path=edge_source,
            body=Edge(parent="DimElemC", component_name="a", weight=1)
        )]
        try:
            self.apply(changeset)
            hierarchy = self.tm1_service.hierarchies.get("TestDimMultiHier", "TestDimMultiHier")
            assert ("DimElemC", "a") not in hierarchy.edges
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)

    def test_apply_modify_edge(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        edge_source = "dimensions/TestDimMultiHier.hierarchies/TestDimMultiHier.json/DimElemC:b"

        changeset = Changeset("modify_edge_case")
        changeset.changes = [Change(
            change_type=ChangeType.MODIFY,
            object_type=ObjectType.EDGE,
            source_path=edge_source,
            body=Edge(parent="DimElemC", component_name="b", weight=2)
        )]
        try:
            self.apply(changeset)
            hierarchy = self.tm1_service.hierarchies.get("TestDimMultiHier", "TestDimMultiHier")
            assert hierarchy.edges.get(("DimElemC", "b")) == 2
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)


    # -----------------------------------------------------------------------
    # Subset tests
    # -----------------------------------------------------------------------

    def test_apply_add_subset(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        subset_name = "zz_temp_subset_add"
        source_path = f"dimensions/TestDim1.hierarchies/TestDim1.subsets/{subset_name}.json"
        try:
            self.tm1_service.subsets.delete(subset_name=subset_name, dimension_name="TestDim1", hierarchy_name="TestDim1")
        except Exception:
            pass

        changeset = Changeset("add_subset_case")
        changeset.changes = [Change(
            change_type=ChangeType.ADD,
            object_type=ObjectType.SUBSET,
            source_path=source_path,
            body=GitSubset(name=subset_name, expression="{[TestDim1].[TestDim1].Members}")
        )]
        try:
            self.apply(changeset)
            subset_obj = self.tm1_service.subsets.get(subset_name=subset_name, dimension_name="TestDim1", hierarchy_name="TestDim1")
            assert subset_obj is not None
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)

    def test_apply_remove_subset(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        subset_name = "zz_temp_subset_remove"
        source_path = f"dimensions/TestDim1.hierarchies/TestDim1.subsets/{subset_name}.json"
        subset_obj = TM1py.Subset(subset_name=subset_name, dimension_name="TestDim1", hierarchy_name="TestDim1",
                                  expression="{[TestDim1].[TestDim1].Members}")
        self.tm1_service.subsets.update_or_create(subset_obj)

        changeset = Changeset("remove_subset_case")
        changeset.changes = [Change(
            change_type=ChangeType.REMOVE,
            object_type=ObjectType.SUBSET,
            source_path=source_path,
            body=GitSubset(name=subset_name, expression="{[TestDim1].[TestDim1].Members}")
        )]
        try:
            self.apply(changeset)
            assert not self.tm1_service.subsets.exists(subset_name=subset_name, dimension_name="TestDim1", hierarchy_name="TestDim1")
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)

    def test_apply_modify_subset(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        subset_name = "zz_temp_subset_modify"
        source_path = f"dimensions/TestDim1.hierarchies/TestDim1.subsets/{subset_name}.json"
        subset_obj = TM1py.Subset(subset_name=subset_name, dimension_name="TestDim1", hierarchy_name="TestDim1",
                                  expression="{[TestDim1].[TestDim1].Members}")
        self.tm1_service.subsets.update_or_create(subset_obj)

        changeset = Changeset("modify_subset_case")
        changeset.changes = [Change(
            change_type=ChangeType.MODIFY,
            object_type=ObjectType.SUBSET,
            source_path=source_path,
            body=GitSubset(name=subset_name, expression="{[TestDim1].[TestDim1].[TestDim1Elem1]}")
        )]
        try:
            self.apply(changeset)
            updated_subset = self.tm1_service.subsets.get(subset_name=subset_name, dimension_name="TestDim1", hierarchy_name="TestDim1")
            assert "TestDim1Elem1" in updated_subset.expression
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)


    # -----------------------------------------------------------------------
    # Hierarchy tests
    # -----------------------------------------------------------------------

    def test_create_hierarchy_no_meta_objects(self):
        """Changeset should re-create a hierarchy that was deleted from the server."""
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)

        # Delete an existing fixture hierarchy so it is missing on the server
        self.tm1_service.hierarchies.delete(
            dimension_name="TestDimMultiHier", hierarchy_name="Hier2"
        )
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # when
        changeset = self.compare(model, fixture_tm1gitpy_model)
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # then
        added_hierarchies = self._changes_by(changeset, ChangeType.ADD, "Hierarchy")
        assert len(added_hierarchies) >= 1
        assert any(h.name == "Hier2" for h in added_hierarchies)
        self.check_no_diff(fixture_tm1gitpy_dir, model)

    def test_delete_hierarchy_no_meta_objects(self):
        """Changeset should remove an extra hierarchy that does not exist in the fixture."""
        # given
        fixture_tm1gitpy_dir, fixture_tm1gitpy_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)

        # Add an alternate hierarchy to an existing fixture dimension
        alt_hierarchy = Hierarchy(
            dimension_name="TestDim1", name="AltHierarchy"
        )
        alt_hierarchy.add_element("AltElement1", "Numeric")
        self.tm1_service.hierarchies.create(alt_hierarchy)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # when
        changeset = self.compare(model, fixture_tm1gitpy_model)
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # then
        removed_hierarchies = self._changes_by(changeset, ChangeType.REMOVE, "Hierarchy")
        assert len(removed_hierarchies) >= 1
        assert any(h.name == "AltHierarchy" for h in removed_hierarchies)
        self.check_no_diff(fixture_tm1gitpy_dir, model)

    def test_compare_child_only_hierarchy_change_does_not_modify_dimension(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)

        alt_hierarchy = Hierarchy(dimension_name="TestDim1", name="AltHierarchyNoParentModify")
        alt_hierarchy.add_element("AltElem", "Numeric")
        self.tm1_service.hierarchies.create(alt_hierarchy)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        try:
            changeset = self.compare(model, fixture_model)
            removed_hierarchies = self._changes_by(changeset, ChangeType.REMOVE, "Hierarchy")
            modified_dimensions = self._changes_by(changeset, ChangeType.MODIFY, "Dimension")

            assert any(h.name == "AltHierarchyNoParentModify" for h in removed_hierarchies)
            assert not modified_dimensions
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)

    def test_compare_ignores_leaf_elements(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        leaf_element_name = "zz_leaf_noise_elem"
        try:
            self.tm1_service.elements.delete(
                hierarchy_name="Leaves",
                dimension_name="TestDimMultiHier",
                element_name=leaf_element_name
            )
        except Exception:
            pass

        self.tm1_service.elements.create(
            hierarchy_name="Leaves",
            dimension_name="TestDimMultiHier",
            element=TM1py.Element(name=leaf_element_name, element_type="Numeric")
        )
        model = export_check_no_errors(self, self._f_no_meta_obj)
        try:
            changeset = self.compare(model, fixture_model)
            leaf_element_changes = [
                c for c in changeset.changes
                if c.body.__class__.__name__ == "Element" and "/Leaves.json/" in c.source_path
            ]
            assert not leaf_element_changes
        finally:
            self._restore_fixture_no_meta(fixture_dir, fixture_model)


    # -----------------------------------------------------------------------
    # Rule tests (rules are part of cubes)
    # -----------------------------------------------------------------------

    def test_delete_rule_no_meta_objects(self):
        """Changeset should remove a rule that was added on the server but is absent in the fixture."""
        # given — fixture TestCube1 has no rules
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)

        # Add a rule to TestCube1 on the server
        cube_object = self.tm1_service.cubes.get("TestCube1")
        cube_object.rules = TM1py.Rules("SKIPCHECK;\n['myelement_num'] = 1;\nFEEDERS;\n")
        self.tm1_service.cubes.update(cube_object)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # then — rule changes are unified into one modify Rule change per cube
        modified_rules = self._changes_by(changeset, ChangeType.MODIFY, "Rule")
        assert len(modified_rules) == 1
        assert modified_rules[0].source_path == "cubes/TestCube1.rules"
        assert modified_rules[0].name == "default"
        assert modified_rules[0].full_statement == ""
        self.check_no_diff(fixture_dir, model)

    def test_create_rule_no_meta_objects(self):
        """Changeset should add a rule that exists in the fixture but is missing on the server."""
        # given — fixture TestCube2WithRule has rules; remove them from server first
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)
        fixture_cube = next(c for c in fixture_model.cubes if c.name == "TestCube2WithRule")
        expected_rule_text = fixture_cube.get_rule_text()

        # Remove rule from TestCube2WithRule to create the expected diff against fixture.
        cube_object = self.tm1_service.cubes.get("TestCube2WithRule")
        cube_object.rules = TM1py.Rules("SKIPCHECK;")
        self.tm1_service.cubes.update(cube_object)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # then — rule changes are unified into one modify Rule change per cube
        modified_rules = self._changes_by(changeset, ChangeType.MODIFY, "Rule")
        target_rules = [rule for rule in modified_rules if rule.source_path == "cubes/TestCube2WithRule.rules"]
        assert len(target_rules) == 1
        assert target_rules[0].name == "default"
        assert target_rules[0].full_statement == expected_rule_text

        # Verify the rule is present on the server
        cube_final = self.tm1_service.cubes.get("TestCube2WithRule")
        assert cube_final.rules is not None
        assert "TestDim1Elem1" in str(cube_final.rules)
        self.check_no_diff(fixture_dir, model)


    # -----------------------------------------------------------------------
    # Apply chain test
    # -----------------------------------------------------------------------

    def test_apply_mixed_changeset_operations(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta_obj)

        temp_hierarchy_name = "TmpHierForChangeset"
        process_name = "zz_test_changeset_apply_proc"
        cube_name = "TestCube3WithView"
        view_name = "testcube3withview_view1"
        native_view_name = "zz_mixed_native_view"
        rule_cube_name = "TestCube2WithRule"

        # Preconditions for deterministic behavior.
        if process_name in self.tm1_service.processes.get_all_names(skip_control_processes=False):
            self.tm1_service.processes.delete(process_name)
        try:
            self.tm1_service.elements.remove_edge("TestDimMultiHier", "TestDimMultiHier", "DimElemC", "DimElem1")
        except Exception:
            pass
        try:
            self.tm1_service.hierarchies.delete(dimension_name="TestDim1", hierarchy_name=temp_hierarchy_name)
        except Exception:
            pass
        try:
            self.tm1_service.views.delete(cube_name=cube_name, view_name=native_view_name)
        except Exception:
            pass
        self.tm1_service.hierarchies.create(Hierarchy(dimension_name="TestDim1", name=temp_hierarchy_name))
        leaves = self.tm1_service.hierarchies.get("TestDimMultiHier", "Leaves")
        if "b" not in leaves.elements:
            self.tm1_service.elements.create(
                hierarchy_name="Leaves",
                dimension_name="TestDimMultiHier",
                element=TM1py.Element(name="b", element_type="Numeric")
            )

        changeset = Changeset(changeset_name="mixed_changeset_ops")
        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.ELEMENT,
                source_path="dimensions/TestDimMultiHier.hierarchies/Leaves.json/b",
                body=Element(
                    name="b",
                    type="Numeric",
                ),
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.EDGE,
                source_path="dimensions/TestDimMultiHier.hierarchies/TestDimMultiHier.json/DimElemC:DimElem1",
                body=Edge(
                    parent="DimElemC",
                    component_name="DimElem1",
                    weight=1,
                ),
            ),
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.HIERARCHY,
                source_path=f"dimensions/TestDim1.hierarchies/{temp_hierarchy_name}.json",
                body=GitHierarchy(
                    name=temp_hierarchy_name,
                    elements=[],
                    edges=[],
                    subsets=[],
                ),
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.MDX_VIEW,
                source_path=f"cubes/{cube_name}.views/{view_name}.json",
                body=MDXView(
                    name=view_name,
                    mdx=f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 FROM [{cube_name}]",
                ),
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.RULE,
                source_path=f"cubes/{rule_cube_name}.rules",
                body=Rule(
                    name="default",
                    area="[default]",
                    full_statement="SKIPCHECK;\n['TestDim1Elem1'] = 2;\n",
                ),
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.NATIVE_VIEW,
                source_path=f"cubes/{cube_name}.views/{native_view_name}.json",
                body=NativeView(
                    name=native_view_name,
                    columns=[{
                        "Subset": {
                            "Expression": "{[TestDim2].[TestDim2].Members}",
                            "Hierarchy": {"@id": "Dimensions('TestDim2')/Hierarchies('TestDim2')"},
                        }
                    }],
                    rows=[{
                        "Subset": {
                            "Expression": "{[TestDim1].[TestDim1].Members}",
                            "Hierarchy": {"@id": "Dimensions('TestDim1')/Hierarchies('TestDim1')"},
                        }
                    }],
                    titles=[],
                    suppress_empty_columns=True,
                    suppress_empty_rows=True,
                    format_string="0.#########",
                ),
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                source_path=f"processes/{process_name}.json",
                body=GitProcess(
                    name=process_name,
                    hasSecurityAccess=False,
                    code_link=f"{process_name}.ti",
                    datasource="None",
                    parameters=[],
                    variables=[],
                    ti=TI("", "", "", ""),
                ),
            ),
        ]

        try:
            self.apply(changeset)

            leaves_hierarchy = self.tm1_service.hierarchies.get("TestDimMultiHier", "Leaves")
            assert "b" not in leaves_hierarchy.elements

            default_hierarchy = self.tm1_service.hierarchies.get("TestDimMultiHier", "TestDimMultiHier")
            assert ("DimElemC", "DimElem1") in default_hierarchy.edges

            testdim1 = self.tm1_service.dimensions.get("TestDim1")
            assert temp_hierarchy_name not in [hier.name for hier in testdim1.hierarchies]

            updated_view = self.tm1_service.views.get_mdx_view(cube_name=cube_name, view_name=view_name)
            assert "TestDim1Elem1" in updated_view.mdx

            updated_cube = self.tm1_service.cubes.get(rule_cube_name)
            assert updated_cube.rules is not None
            assert " = 2;" in str(updated_cube.rules)

            created_native_view = self.tm1_service.views.get_native_view(cube_name=cube_name, view_name=native_view_name)
            assert created_native_view is not None

            assert process_name in self.tm1_service.processes.get_all_names(skip_control_processes=False)
        finally:
            # Restore fixture baseline to keep integration tests isolated.
            self._restore_fixture_no_meta(fixture_dir, fixture_model)


    def compare(self, source, target, mode :str = 'full'):
        comparator = Comparator()
        return comparator.compare(source, target, mode=mode)
        
    
    def apply(self, changeset: Changeset):
        changeset.sort()
        status_dir = 'tests'
        exec_id = 'test_create_and_delete'
        success, _errors = changeset.apply(
            tm1_service=self.tm1_service,
            status_dir=status_dir,
            execution_id=exec_id
        )
        assert success, f"Changeset application failed with errors: {_errors}"


    def check_no_diff(self, expected_dir, model):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = str(Path(temp_dir) / "exported_model")
            serialize_model(model, export_dir)
            assert_export_matches_expected_subdirs(export_dir, expected_dir)
