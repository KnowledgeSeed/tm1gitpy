import logging
from uuid import uuid4

import TM1py
import pytest
from TM1py import Cube, Dimension, Hierarchy, TM1Service

from test_integration.test_base import (
    DEFAULT_MAX_WORKERS,
    export_check_no_errors,
    load_fixture_model_tm1gitpy,
    tm1_service,
    check_no_diff,
)
from tm1_git_py.db.changeset_store import ChangesetStore
from tm1_git_py.services.changeset import ChangeType, Changeset, Change, ObjectType
from tm1_git_py.services.comparator import Comparator
from tm1_git_py.services.deserializer import deserialize_model
from tm1_git_py.services.filter import DEFAULT_TM1_TECHNICAL_OBJECTS
from tm1_git_py.services.filter import filter as filter_model
from tm1_git_py.model.edge import Edge
from tm1_git_py.model.element import Element
from tm1_git_py.model.hierarchy import Hierarchy as GitHierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.model.process import Process
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.subset import Subset as GitSubset
from tm1_git_py.model.ti import TI


@pytest.mark.usefixtures("tm1_service")
class TestChangesetApply:

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

    def test_create_cube_full_no_meta_objects(self):

        ChangesetStore.for_changeset_id(changeset_id="asd")
        Changeset()
        Changeset("ssdfsdf")._active_store()
        Changeset("sasdasd")._active_store()

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCube1"

        self.tm1_service.cubes.delete(cube_name)
        test_model = export_check_no_errors(self)
    
        # when
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)

        # then
        added_cubes = self._changes_by(changeset, ChangeType.ADD, "Cube")
        assert len(added_cubes) == 1
        assert added_cubes[0].name == cube_name
        assert self.tm1_service.cubes.exists(cube_name)
        test_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(fixture_dir, test_model)

    def test_create_cube_full_with_meta_objects(self):
        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, filter_rules=None, model_id=self._fixture_model_id_with_meta
        )
        cube_name = "TestCube1"

        self.tm1_service.cubes.delete(cube_name)
        test_model = export_check_no_errors(self, self._f_with_meta)
        # when
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)

        # then
        added_cubes = self._changes_by(changeset, ChangeType.ADD, "Cube")
        assert len(added_cubes) == 1
        assert added_cubes[0].name == cube_name
        assert self.tm1_service.cubes.exists(cube_name)
        restored_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(expected_dir=fixture_dir, model=restored_model)

    @pytest.mark.skip
    def test_recreate_cube_with_force_included_technical_objects_ignores_duplicate_adds(
        self, caplog
    ):
        # given
        cube_name = "TestCube1"
        technical_element_uri = Element.uri_for("}Cubes", "}Cubes", cube_name)
        filter_rules = [
            "Cubes('}*')",
            "Dimensions('}*')",
            "Processes('}*')",
            f"!{technical_element_uri}",
        ]
        fixture_dir, full_fixture_model = load_fixture_model_tm1gitpy(
            self, model_id=self._fixture_model_id_no_meta
        )
        fixture_model, _errors = deserialize_model(
            fixture_dir,
            model_id=f"filtered_{uuid4().hex}",
            max_workers=DEFAULT_MAX_WORKERS,
        )
        fixture_model = filter_model(fixture_model, filter_rules)

        self.tm1_service.cubes.delete(cube_name)
        test_model = export_check_no_errors(self)

        # when
        changeset = self.compare(test_model, fixture_model, filter_rules=filter_rules)

        added_cubes = self._changes_by(changeset, ChangeType.ADD, "Cube")

        technical_add = next(
            change
            for change in changeset.changes
            if change.change_type == ChangeType.ADD
            and change.object_type == ObjectType.ELEMENT
            and change.uri == technical_element_uri
        )

        # Duplicate the real technical add change so TM1 returns "already exists"
        # on the second create and apply must continue.
        changeset.changes.append(
            Change(
                change_type=technical_add.change_type,
                object_type=technical_add.object_type,
                uri=technical_add.uri,
                body=technical_add.body,
            )
        )

        with caplog.at_level(logging.WARNING):
            self.apply(changeset)
        test_model = export_check_no_errors(self, filter_rules)
        remaining_changeset = self.compare(
            test_model, fixture_model, filter_rules=filter_rules
        )

        # then
        assert len(added_cubes) == 1
        assert added_cubes[0].name == cube_name
        assert self.tm1_service.cubes.exists(cube_name)
        assert any(
            "Ignoring duplicate create failure for technical object" in record.message
            and technical_element_uri in record.message
            for record in caplog.records
        )
        assert not remaining_changeset.has_changes()
        restored_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(expected_dir=fixture_dir, model=restored_model)

    def test_create_cube_add_only_no_meta_objects(self):

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCube1"

        self.tm1_service.cubes.delete(cube_name)
        test_model = export_check_no_errors(self)

        # when
        changeset = self.compare(test_model, fixture_model, mode="add_only")
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)

        # then
        added_cubes = self._changes_by(changeset, ChangeType.ADD, "Cube")
        assert len(added_cubes) == 1
        assert added_cubes[0].name == cube_name
        assert self.tm1_service.cubes.exists(cube_name)
        check_no_diff(fixture_dir, test_model)

    def test_create_cube_add_only_with_meta_objects(self):

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, model_id=self._fixture_model_id_with_meta
        )
        cube_name = "TestCube1"

        self.tm1_service.cubes.delete(cube_name)
        test_model = export_check_no_errors(self, self._f_with_meta)

        # when
        changeset = self.compare(test_model, fixture_model, mode="add_only")
        self.apply(changeset)

        # then
        added_cubes = self._changes_by(changeset, ChangeType.ADD, "Cube")
        assert len(added_cubes) == 1
        assert added_cubes[0].name == cube_name
        assert self.tm1_service.cubes.exists(cube_name)
        restored_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(expected_dir=fixture_dir, model=restored_model)

    def test_delete_cube_full_no_meta_objects(self):

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCubeRemovable1"

        self.tm1_service.cubes.create(
            Cube(cube_name, dimensions=["TestDim1", "TestDim2"])
        )
        test_model = export_check_no_errors(self, self._f_no_meta)

        # when
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)

        # then
        removed_cubes = self._changes_by(changeset, ChangeType.REMOVE, "Cube")
        assert len(removed_cubes) == 1
        assert removed_cubes[0].name == cube_name
        assert not self.tm1_service.cubes.exists(cube_name)
        check_no_diff(fixture_dir, test_model)

    def test_delete_cube_full_with_meta_objects(self):

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, model_id=self._fixture_model_id_with_meta
        )
        cube_name = "TestCubeRemovable2"
        filter_rules = [
            "Dimensions('}Cubes')/Hierarchies('}Cubes')/Elements('*')",
            "Dimensions('}Dimensions')/Hierarchies('}Dimensions')/Elements('*')",
            "Dimensions('}*')",
        ]

        self.tm1_service.cubes.create(
            Cube(cube_name, dimensions=["TestDim1", "TestDim2"])
        )
        test_model = export_check_no_errors(self, self._f_with_meta)

        # when
        changeset = self.compare(test_model, fixture_model, filter_rules=filter_rules)
        self.apply(changeset)

        # then
        removed_cubes = self._changes_by(changeset, ChangeType.REMOVE, "Cube")
        assert len(removed_cubes) == 1
        assert removed_cubes[0].name == cube_name
        assert not self.tm1_service.cubes.exists(cube_name)
        restored_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(expected_dir=fixture_dir, model=restored_model)

    def test_delete_cube_add_only_no_meta_objects(self):

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCubeRemovable3"

        self.tm1_service.cubes.create(
            Cube(cube_name, dimensions=["TestDim1", "TestDim2"])
        )
        test_model = export_check_no_errors(self)

        # when
        changeset = self.compare(test_model, fixture_model, mode="add_only")
        self.apply(changeset)

        # then
        assert not self._changes_by(changeset, ChangeType.REMOVE, "Cube")
        assert self.tm1_service.cubes.exists(cube_name)
        self.tm1_service.cubes.delete(cube_name)

    def test_delete_cube_add_only_with_meta_objects(self):
        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, model_id=self._fixture_model_id_with_meta
        )
        cube_name = "TestCubeRemovable4"

        filter_rules = [
            "Dimensions('}Cubes')/Hierarchies('}Cubes')/Elements('*')",
            "Dimensions('}Dimensions')/Hierarchies('}Dimensions')/Elements('*')",
            "Dimensions('}*')",
        ]

        self.tm1_service.cubes.create(
            Cube(cube_name, dimensions=["TestDim1", "TestDim2"])
        )

        # when
        test_model = export_check_no_errors(self, self._f_with_meta)
        changeset = self.compare(
            test_model, fixture_model, mode="add_only", filter_rules=filter_rules
        )
        self.apply(changeset)

        # then
        assert not self._changes_by(changeset, ChangeType.REMOVE, "Cube")
        assert self.tm1_service.cubes.exists(cube_name)

        # cleanup
        self.tm1_service.cubes.delete(cube_name)

    # -----------------------------------------------------------------------
    # View tests
    # -----------------------------------------------------------------------

    def test_apply_add_mdxview(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCube3WithView"
        fixture_cube = next(
            cube for cube in fixture_model.cubes if cube.name == cube_name
        )
        fixture_mdx = next(
            view for view in fixture_cube.views if isinstance(view, MDXView)
        )

        self.tm1_service.views.delete(cube_name=cube_name, view_name=fixture_mdx.name)
        test_model = export_check_no_errors(self)

        changeset = self.compare(test_model, fixture_model)
        added_mdx = self._changes_by(changeset, ChangeType.ADD, "MDXView")

        assert any(view.name == fixture_mdx.name for view in added_mdx)

        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(fixture_dir, test_model)

    def test_apply_add_nativeview(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCube3WithView"
        fixture_cube = next(
            cube for cube in fixture_model.cubes if cube.name == cube_name
        )
        fixture_native = next(
            view for view in fixture_cube.views if isinstance(view, NativeView)
        )

        self.tm1_service.views.delete(
            cube_name=cube_name, view_name=fixture_native.name
        )
        test_model = export_check_no_errors(self)

        changeset = self.compare(test_model, fixture_model)
        added_native = self._changes_by(changeset, ChangeType.ADD, "NativeView")

        assert any(view.name == fixture_native.name for view in added_native)

        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(fixture_dir, test_model)

    def test_apply_remove_mdxview(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCube3WithView"
        view_name = "zz_temp_mdx_view_remove"
        self.tm1_service.views.create(
            TM1py.MDXView(
                cube_name=cube_name,
                view_name=view_name,
                MDX=f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 FROM [{cube_name}]",
            )
        )

        changeset = Changeset("remove_mdxview_case")
        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.MDX_VIEW,
                uri=MDXView.uri_for(cube_name, view_name),
                body=MDXView(name=view_name, mdx=""),
            )
        ]
        self.apply(changeset)
        exists_private_public = self.tm1_service.views.exists(
            cube_name=cube_name, view_name=view_name
        )
        assert not any(exists_private_public)
        test_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(fixture_dir, test_model)

    def test_apply_remove_nativeview(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCube3WithView"
        view_name = "zz_temp_native_view_remove"
        nv = TM1py.NativeView.from_dict(
            view_as_dict={
                "Name": view_name,
                "Columns": [
                    {
                        "Subset": {
                            "Expression": "{[TestDim2].[TestDim2].Members}",
                            "Hierarchy@odata.bind": "Dimensions('TestDim2')/Hierarchies('TestDim2')",
                        }
                    }
                ],
                "Rows": [
                    {
                        "Subset": {
                            "Expression": "{[TestDim1].[TestDim1].Members}",
                            "Hierarchy@odata.bind": "Dimensions('TestDim1')/Hierarchies('TestDim1')",
                        }
                    }
                ],
                "Titles": [],
                "SuppressEmptyColumns": True,
                "SuppressEmptyRows": True,
                "FormatString": "0.#########",
            },
            cube_name=cube_name,
        )
        self.tm1_service.views.create(nv)

        changeset = Changeset("remove_nativeview_case")
        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.NATIVE_VIEW,
                uri=NativeView.uri_for(cube_name, view_name),
                body=NativeView(
                    name=view_name,
                    columns=[],
                    rows=[],
                    titles=[],
                    suppress_empty_columns=True,
                    suppress_empty_rows=True,
                    format_string="0.#########",
                ),
            )
        ]
        self.apply(changeset)
        exists_private_public = self.tm1_service.views.exists(
            cube_name=cube_name, view_name=view_name
        )
        assert not any(exists_private_public)
        test_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(fixture_dir, test_model)

    def test_apply_modify_nativeview(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCube3WithView"
        view_name = "TestCube3WithView_view2"

        changeset = Changeset("modify_nativeview_case")
        changeset.changes = [
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.NATIVE_VIEW,
                uri=NativeView.uri_for(cube_name, view_name),
                body=NativeView(
                    name=view_name,
                    columns=[
                        {
                            "Subset": {
                                "Expression": "{[TestDim2].[TestDim2].Members}",
                                "Hierarchy": {
                                    "@id": "Dimensions('TestDim2')/Hierarchies('TestDim2')"
                                },
                            }
                        }
                    ],
                    rows=[
                        {
                            "Subset": {
                                "Expression": "{[TestDim1].[TestDim1].[TestDim1Elem1]}",
                                "Hierarchy": {
                                    "@id": "Dimensions('TestDim1')/Hierarchies('TestDim1')"
                                },
                            }
                        }
                    ],
                    titles=[],
                    suppress_empty_columns=True,
                    suppress_empty_rows=False,
                    format_string="0.#########",
                ),
            )
        ]
        self.apply(changeset)
        updated = self.tm1_service.views.get_native_view(
            cube_name=cube_name, view_name=view_name
        )
        assert updated.suppress_empty_rows is False

        # clean-up
        test_model = export_check_no_errors(self)
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(fixture_dir, test_model)

    # -----------------------------------------------------------------------
    # Dimension tests
    # -----------------------------------------------------------------------

    def test_create_dimension_no_meta_objects(self):

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        dimension_name = "TestDim3"

        self.tm1_service.dimensions.delete(dimension_name)
        test_model = export_check_no_errors(self)

        # when
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)

        assert self.tm1_service.dimensions.exists(dimension_name)
        check_no_diff(fixture_dir, test_model)

    def test_create_dimension_with_meta_objects(self):

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, model_id=self._fixture_model_id_with_meta
        )
        dimension_name = "TestDim3"
        filter_rules = [
            "Dimensions('}Dimensions')/Hierarchies('}Dimensions')/Elements('*')",
            "Dimensions('}*')",
        ]

        self.tm1_service.dimensions.delete(dimension_name)
        test_model = export_check_no_errors(self, self._f_with_meta)

        # when
        changeset = self.compare(test_model, fixture_model, filter_rules=filter_rules)
        self.apply(changeset)

        assert self.tm1_service.dimensions.exists(dimension_name)
        restored_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(expected_dir=fixture_dir, model=restored_model)

    def test_delete_dimension_no_meta_objects(self):

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        dimension_name = "TestDimension"

        dimension = Dimension(dimension_name)
        dimension.add_hierarchy(
            Hierarchy(dimension_name=dimension_name, name=dimension_name)
        )

        self.tm1_service.dimensions.create(dimension)
        test_model = export_check_no_errors(self)

        # when
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)

        removed_dimensions = self._changes_by(changeset, ChangeType.REMOVE, "Dimension")
        assert len(removed_dimensions) == 1
        assert removed_dimensions[0].name == dimension_name
        assert not self.tm1_service.dimensions.exists(dimension_name)
        check_no_diff(fixture_dir, test_model)

    def test_delete_dimension_with_meta_objects(self):

        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, [], model_id=self._fixture_model_id_with_meta
        )
        dimension_name = "TestDimension"
        filter_rules = [
            "Dimensions('}Dimensions')/Hierarchies('}Dimensions')/Elements('*')",
            "Dimensions('}*')",
        ]

        dimension = Dimension(dimension_name)
        dimension.add_hierarchy(
            Hierarchy(dimension_name=dimension_name, name=dimension_name)
        )

        self.tm1_service.dimensions.create(dimension)
        test_model = export_check_no_errors(self, self._f_with_meta)

        # when
        changeset = self.compare(test_model, fixture_model, filter_rules=filter_rules)
        self.apply(changeset)

        removed_dimensions = self._changes_by(changeset, ChangeType.REMOVE, "Dimension")
        assert len(removed_dimensions) == 1
        assert removed_dimensions[0].name == dimension_name
        assert not self.tm1_service.dimensions.exists(dimension_name)
        restored_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(expected_dir=fixture_dir, model=restored_model)

    # -----------------------------------------------------------------------
    # Element and Edge tests
    # -----------------------------------------------------------------------

    def test_apply_add_element(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        element_name = "TestDim1Elem1"
        self.tm1_service.elements.delete(
            hierarchy_name="TestDim1",
            dimension_name="TestDim1",
            element_name=element_name,
        )
        test_model = export_check_no_errors(self)
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        added = self.tm1_service.elements.get(
            dimension_name="TestDim1",
            hierarchy_name="TestDim1",
            element_name=element_name,
        )
        assert added is not None

    def test_apply_remove_edge(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        self.tm1_service.elements.add_edges(
            "TestDimMultiHier", "TestDimMultiHier", {("DimElemC", "DimElem1"): 1}
        )

        test_model = export_check_no_errors(self)
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        hierarchy = self.tm1_service.hierarchies.get(
            "TestDimMultiHier", "TestDimMultiHier"
        )
        assert ("DimElemC", "DimElem1") not in hierarchy.edges

    def test_apply_modify_edge(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        hierarchy = self.tm1_service.hierarchies.get(
            "TestDimMultiHier", "TestDimMultiHier"
        )
        hierarchy.update_edge(parent="DimElemC", component="b", weight=2)

        self.tm1_service.hierarchies.update(hierarchy)
        assert hierarchy.edges.get(("DimElemC", "b")) == 2

        test_model = export_check_no_errors(self)
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        hierarchy = self.tm1_service.hierarchies.get(
            "TestDimMultiHier", "TestDimMultiHier"
        )
        assert hierarchy.edges.get(("DimElemC", "b")) == 1

    # -----------------------------------------------------------------------
    # Subset tests
    # -----------------------------------------------------------------------

    @pytest.mark.skip
    def test_apply_add_subset(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, model_id=self._fixture_model_id_no_meta
        )
        subset_name = "zz_temp_subset_add"
        self.tm1_service.subsets.delete(
            subset_name=subset_name,
            dimension_name="TestDim1",
            hierarchy_name="TestDim1",
        )
        changeset = Changeset("add_subset_case")
        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=GitSubset.uri_for("TestDim1", "TestDim1", subset_name),
                body=GitSubset(
                    name=subset_name, expression="{[TestDim1].[TestDim1].Members}"
                ),
            )
        ]
        self.apply(changeset)
        subset_obj = self.tm1_service.subsets.get(
            subset_name=subset_name,
            dimension_name="TestDim1",
            hierarchy_name="TestDim1",
        )
        assert subset_obj is not None

    @pytest.mark.skip
    def test_apply_remove_subset(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        subset_name = "zz_temp_subset_remove"
        subset_obj = TM1py.Subset(
            subset_name=subset_name,
            dimension_name="TestDim1",
            hierarchy_name="TestDim1",
            expression="{[TestDim1].[TestDim1].Members}",
        )
        self.tm1_service.subsets.create(subset_obj)

        test_model = export_check_no_errors(self)
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        assert not self.tm1_service.subsets.exists(
            subset_name=subset_name,
            dimension_name="TestDim1",
            hierarchy_name="TestDim1",
        )

        # clean-up
        self.tm1_service.hierarchies.delete("}Subsets_TestDim1", "}Subsets_TestDim1")
        self.tm1_service.dimensions.delete("}Subsets_TestDim1")
        test_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(fixture_dir, test_model)

    def test_apply_modify_subset(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        subset_name = "zz_temp_subset_modify"
        subset_obj = TM1py.Subset(
            subset_name=subset_name,
            dimension_name="TestDim1",
            hierarchy_name="TestDim1",
            expression="{[TestDim1].[TestDim1].Members}",
        )
        self.tm1_service.subsets.update_or_create(subset_obj)

        changeset = Changeset("modify_subset_case")
        changeset.changes = [
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.SUBSET,
                uri=GitSubset.uri_for("TestDim1", "TestDim1", subset_name),
                body=GitSubset(
                    name=subset_name,
                    expression="{[TestDim1].[TestDim1].[TestDim1Elem1]}",
                ),
            )
        ]
        try:
            self.apply(changeset)
            updated_subset = self.tm1_service.subsets.get(
                subset_name=subset_name,
                dimension_name="TestDim1",
                hierarchy_name="TestDim1",
            )
            assert "TestDim1Elem1" in updated_subset.expression
        finally:
            # clean-up
            self.tm1_service.hierarchies.delete("}Subsets_TestDim1", "}Subsets_TestDim1")
            self.tm1_service.dimensions.delete("}Subsets_TestDim1")
            test_model = export_check_no_errors(self)
            changeset = self.compare(test_model, fixture_model)
            self.apply(changeset)
            test_model = export_check_no_errors(self, self._f_with_meta)
            check_no_diff(fixture_dir, test_model)

    # -----------------------------------------------------------------------
    # Hierarchy tests
    # -----------------------------------------------------------------------

    def test_create_hierarchy_no_meta_objects(self):
        """Changeset should re-create a hierarchy that was deleted from the server."""
        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        dimension_name = "TestDimMultiHier"
        hierarchy_name = "Hier2"

        # Delete an existing fixture hierarchy so it is missing on the server
        self.tm1_service.hierarchies.delete(
            dimension_name=dimension_name, hierarchy_name=hierarchy_name
        )
        test_model = export_check_no_errors(self)

        # when
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)

        # then
        added_hierarchies = self._changes_by(changeset, ChangeType.ADD, "Hierarchy")
        assert len(added_hierarchies) >= 1
        assert any(h.name == hierarchy_name for h in added_hierarchies)
        assert self.tm1_service.hierarchies.exists(
            dimension_name=dimension_name, hierarchy_name=hierarchy_name
        )
        check_no_diff(fixture_dir, test_model)

    def test_delete_hierarchy_no_meta_objects(self):
        """Changeset should remove an extra hierarchy that does not exist in the fixture."""
        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        dimension_name = "TestDim1"
        hierarchy_name = "AltHierarchy"

        # Add an alternate hierarchy to an existing fixture dimension
        alt_hierarchy = Hierarchy(dimension_name=dimension_name, name=hierarchy_name)
        alt_hierarchy.add_element("AltElement1", "Numeric")
        self.tm1_service.hierarchies.create(alt_hierarchy)
        test_model = export_check_no_errors(self)

        # when
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)

        # then
        removed_hierarchies = self._changes_by(
            changeset, ChangeType.REMOVE, "Hierarchy"
        )
        assert len(removed_hierarchies) >= 1
        assert any(h.name == hierarchy_name for h in removed_hierarchies)
        assert not self.tm1_service.hierarchies.exists(
            dimension_name=dimension_name, hierarchy_name=hierarchy_name
        )
        check_no_diff(fixture_dir, test_model)

    def test_compare_child_only_hierarchy_change_does_not_modify_dimension(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )

        alt_hierarchy = Hierarchy(
            dimension_name="TestDim1", name="AltHierarchyNoParentModify"
        )
        alt_hierarchy.add_element("AltElem", "Numeric")
        self.tm1_service.hierarchies.create(alt_hierarchy)
        model = export_check_no_errors(self)

        changeset = self.compare(model, fixture_model)
        removed_hierarchies = self._changes_by(
            changeset, ChangeType.REMOVE, "Hierarchy"
        )
        modified_dimensions = self._changes_by(
            changeset, ChangeType.MODIFY, "Dimension"
        )

        assert any(h.name == "AltHierarchyNoParentModify" for h in removed_hierarchies)
        assert not modified_dimensions

    def test_compare_ignores_leaf_elements(self):
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        leaf_element_name = "zz_leaf_noise_elem"
        try:
            self.tm1_service.elements.create(
                hierarchy_name="Leaves",
                dimension_name="TestDimMultiHier",
                element=TM1py.Element(name=leaf_element_name, element_type="Numeric"),
            )
        except Exception:
            pass

        test_model = export_check_no_errors(self)
        filter_rules = list(self._f_no_meta)
        filter_rules.append(
            "Dimensions('TestDimMultiHier')/Hierarchies('Leaves')/Elements('*')"
        )
        changeset = self.compare(test_model, fixture_model, filter_rules=filter_rules)

        leaf_element_uri = Element.uri_for(
            "TestDimMultiHier", "Leaves", leaf_element_name
        )
        leaf_element_changes = [
            c
            for c in changeset.changes
            if c.object_type == ObjectType.ELEMENT and c.uri == leaf_element_uri
        ]
        assert not leaf_element_changes

    # -----------------------------------------------------------------------
    # Rule tests (rules are part of cubes)
    # -----------------------------------------------------------------------

    def test_delete_rule_no_meta_objects(self):
        """Changeset should clear rules on the server and no-meta restore should bring them back."""
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCube2WithRule"

        cube_object = self.tm1_service.cubes.get(cube_name)
        cube_object.rules = TM1py.Rules("")
        self.tm1_service.cubes.update(cube_object)

        changeset = Changeset("delete_rule_case")
        changeset.changes = [
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.RULE,
                uri=Rule.uri_for(cube_name),
                body=Rule(name="default", area="[default]", full_statement=""),
            )
        ]
        self.apply(changeset)

        cube_after = self.tm1_service.cubes.get(cube_name)
        assert cube_after.rules is None

        # clean-up
        test_model = export_check_no_errors(self)
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(fixture_dir, test_model)

    def test_create_rule_no_meta_objects(self):
        """Changeset should add a rule that exists in the fixture but is missing on the server."""
        # given — fixture TestCube2WithRule has rules; remove them from server first
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(
            self, self._f_no_meta, model_id=self._fixture_model_id_no_meta
        )
        cube_name = "TestCube2WithRule"
        expected_rule_text = self.tm1_service.cubes.get(cube_name).rules.text

        # Remove rule from TestCube2WithRule to create the expected diff against fixture.
        cube_object = self.tm1_service.cubes.get("TestCube2WithRule")
        cube_object.rules = TM1py.Rules("SKIPCHECK;")
        self.tm1_service.cubes.update(cube_object)
        test_model = export_check_no_errors(self)

        # when
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self)

        # then — rule changes are unified into one modify Rule change per cube
        modified_rules = self._changes_by(changeset, ChangeType.MODIFY, "Rule")
        target_rules = [
            rule for rule in modified_rules if rule.full_statement == expected_rule_text
        ]
        assert len(target_rules) == 1
        assert target_rules[0].name == "default"
        assert target_rules[0].full_statement == expected_rule_text

        # Verify the rule is present on the server
        cube_final = self.tm1_service.cubes.get("TestCube2WithRule")
        assert cube_final.rules is not None
        assert "TestDim1Elem1" in str(cube_final.rules)

        # clean-up
        test_model = export_check_no_errors(self)
        changeset = self.compare(test_model, fixture_model)
        self.apply(changeset)
        test_model = export_check_no_errors(self, self._f_with_meta)
        check_no_diff(fixture_dir, test_model)

    # -----------------------------------------------------------------------
    # Apply chain test
    # -----------------------------------------------------------------------

    def test_apply_mixed_changeset_operations(self):
        temp_hierarchy_name = "TmpHierForChangeset"
        process_name = "zz_test_changeset_apply_proc"
        cube_name = "TestCube3WithView"
        view_name = "testcube3withview_view1"
        native_view_name = "zz_mixed_native_view"
        rule_cube_name = "TestCube2WithRule"

        # Preconditions for deterministic behaviour.
        if process_name in self.tm1_service.processes.get_all_names(
            skip_control_processes=False
        ):
            self.tm1_service.processes.delete(process_name)
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
        self.tm1_service.hierarchies.create(
            Hierarchy(dimension_name="TestDim1", name=temp_hierarchy_name)
        )
        leaves = self.tm1_service.hierarchies.get("TestDimMultiHier", "Leaves")
        if "b" not in leaves.elements:
            self.tm1_service.elements.create(
                hierarchy_name="Leaves",
                dimension_name="TestDimMultiHier",
                element=TM1py.Element(name="b", element_type="Numeric"),
            )

        changeset = Changeset()
        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.ELEMENT,
                uri=Element.uri_for("TestDimMultiHier", "Leaves", "b"),
                body=Element(
                    name="b",
                    type="Numeric",
                ),
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.EDGE,
                uri=Edge.uri_for(
                    "TestDimMultiHier",
                    "TestDimMultiHier",
                    "DimElemC",
                    "DimElem1",
                ),
                body=Edge(
                    parent="DimElemC",
                    component_name="DimElem1",
                    weight=1,
                ),
            ),
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.HIERARCHY,
                uri=GitHierarchy.uri_for("TestDim1", temp_hierarchy_name),
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
                uri=MDXView.uri_for(cube_name, view_name),
                body=MDXView(
                    name=view_name,
                    mdx=f"SELECT {{[TestDim1].[TestDim1].[TestDim1Elem1]}} ON 0 FROM [{cube_name}]",
                ),
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.RULE,
                uri=Rule.uri_for(rule_cube_name),
                body=Rule(
                    name="default",
                    area="[default]",
                    full_statement="SKIPCHECK;\n['TestDim1Elem1'] = 2;\n",
                ),
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.NATIVE_VIEW,
                uri=NativeView.uri_for(cube_name, native_view_name),
                body=NativeView(
                    name=native_view_name,
                    columns=[
                        {
                            "Subset": {
                                "Expression": "{[TestDim2].[TestDim2].Members}",
                                "Hierarchy": {
                                    "@id": "Dimensions('TestDim2')/Hierarchies('TestDim2')"
                                },
                            }
                        }
                    ],
                    rows=[
                        {
                            "Subset": {
                                "Expression": "{[TestDim1].[TestDim1].Members}",
                                "Hierarchy": {
                                    "@id": "Dimensions('TestDim1')/Hierarchies('TestDim1')"
                                },
                            }
                        }
                    ],
                    titles=[],
                    suppress_empty_columns=True,
                    suppress_empty_rows=True,
                    format_string="0.#########",
                ),
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=Process.uri_for(process_name),
                body=Process(
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

        self.apply(changeset)

        leaves_hierarchy = self.tm1_service.hierarchies.get(
            "TestDimMultiHier", "Leaves"
        )
        assert "b" not in leaves_hierarchy.elements

        default_hierarchy = self.tm1_service.hierarchies.get(
            "TestDimMultiHier", "TestDimMultiHier"
        )
        assert ("DimElemC", "DimElem1") in default_hierarchy.edges

        testdim1 = self.tm1_service.dimensions.get("TestDim1")
        assert temp_hierarchy_name not in [hier.name for hier in testdim1.hierarchies]

        updated_view = self.tm1_service.views.get_mdx_view(
            cube_name=cube_name, view_name=view_name
        )
        assert "TestDim1Elem1" in updated_view.mdx

        updated_cube = self.tm1_service.cubes.get(rule_cube_name)
        assert updated_cube.rules is not None
        assert " = 2;" in str(updated_cube.rules)

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
        return comparator.compare(source, target, mode=mode, filter_rules=filter_rules)

    def apply(self, changeset: Changeset):
        status_dir = "test_integration"
        exec_id = "test_create_and_delete"
        success, _errors = changeset.apply(
            tm1_service=self.tm1_service, status_dir=status_dir, execution_id=exec_id
        )
        assert success, f"Changeset application failed with errors: {_errors}"
