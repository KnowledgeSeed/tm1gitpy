import os.path
import types
from pathlib import Path
from typing import TypeVar

import pytest
import yaml

import tm1_git_py.apply as apply_module
import tm1_git_py.comparator
from tests.utility import (
    _build_mock_changeset_data,
    _objects_equal_case_builders,
    build_mock_model,
    dim_data,
    chore_data,
    process_data,
    make_dimension, make_subset, make_chore, make_process, make_mdx_view, make_cube, make_rule, make_hierarchy,
    make_element
)
from tm1_git_py.apply import apply, apply_with_atomic_schema
from tm1_git_py.exporter import export
from tm1_git_py.serializer import serialize_model
from tm1_git_py.comparator import Comparator
from tm1_git_py.changeset import Change, ChangeType, Changeset, ObjectType, import_changeset
from tm1_git_py.filter import filter_changeset, should_exclude_path
from tm1_git_py.deserializer import *
from tm1_git_py.model import *
from tm1_git_py.model import dimension, hierarchy, subset, chore, process, cube, mdxview, edge, element, nativeview
from tm1_git_py.model.nativeview import NativeView

T = TypeVar('T', Cube, Dimension, Process, Chore)


TEST_ROOT = Path(__file__).resolve().parent
test_model_dir_base = TEST_ROOT / "model_test_export" / "test_model_base"
test_model_dir_diff = TEST_ROOT / "model_test_export" / "test_model_diff"


@pytest.fixture(params=list(_objects_equal_case_builders().keys()), ids=list(_objects_equal_case_builders().keys()))
def objects_equal_data(request):
    builders = _objects_equal_case_builders()
    return builders[request.param]()



class TestDeserializer:

    def test_deserialize_chores(self, chores_dir=test_model_dir_base / 'chores'):
        chores, errors = deserialize_chores(chore_dir=chores_dir)
        for chore in chores.values():
            assert isinstance(chore, Chore)


    def test_deserialize_dimensions(self, dimensions_dir=test_model_dir_base / 'dimensions'):
        dimensions, errors = deserialize_dimensions(dimension_dir=dimensions_dir)
        for dimension in dimensions.values():
            assert isinstance(dimension, Dimension)


    def test_deserialize_dimension_with_children(self, dimensions_dir=test_model_dir_base / 'dimensions'):
        dimensions, errors = deserialize_dimensions(dimension_dir=dimensions_dir)
        dim_version = dimensions.get('testbenchVersion')
        hier_version = dim_version.hierarchies[0]
        assert dim_version.name == 'testbenchVersion'
        assert hier_version.name == 'testbenchVersion'
        assert hier_version.elements[0].to_dict() == {"name": "Actual", "type": "Numeric"}


    def test_deserialize_cubes(self, cubes_dir=test_model_dir_base / 'cubes'):
        expected_cube_names = ['testbenchSales']
        dimensions, errors = deserialize_dimensions(test_model_dir_base / 'dimensions')
        cubes, errors = deserialize_cubes(cubes_dir=cubes_dir, _dimensions=dimensions)
        diff_cube_names = set(expected_cube_names) - set(cubes.keys())
        assert len(diff_cube_names) == 0


    def test_deserialize_process(self, processes_dir=test_model_dir_base / 'processes'):
        processes, errors = deserialize_processes(process_dir=processes_dir)
        for process in processes.values():
            assert isinstance(process, Process)


    @pytest.mark.parametrize("data", dim_data)
    def test_deserialize_dimensions_error_propagation(self, tmp_path, data):
        dimensions_dir = tmp_path / "dimensions"
        dimensions_dir.mkdir()
        broken_dims = dimensions_dir / f"BrokenDimension.json"

        broken_dims.write_text(data, encoding="utf-8")

        dimensions, errors = deserialize_dimensions(dimensions_dir)

        assert not dimensions, f"Broken {type(dimensions.values())} file should not deserialize successfully"
        expected_key = Dimension.as_link(broken_dims.name)
        assert expected_key in errors, (
            f"Error key '{expected_key}' missing; collected keys: {list(errors.keys())}"
        )


    @pytest.mark.parametrize("data", chore_data)
    def test_deserialize_chore_error_propagation(self, tmp_path, data):
        chores_dir = tmp_path / "chores"
        chores_dir.mkdir()
        broken_chore = chores_dir / f"BrokenChores.json"

        broken_chore.write_text(data, encoding="utf-8")

        chores, errors = deserialize_chores(chores_dir)

        assert not chores, f"Broken {type(chores.values())} file should not deserialize successfully"
        expected_key = Chore.as_link(broken_chore.name)
        assert expected_key in errors, (
            f"Error key '{expected_key}' missing; collected keys: {list(errors.keys())}"
        )


    @pytest.mark.parametrize("data", process_data)
    def test_deserialize_process_error_propagation(self, tmp_path, data):
        processes_dir = tmp_path / "processes"
        processes_dir.mkdir()
        broken_process = processes_dir / f"BrokenProcess.json"

        broken_process.write_text(data, encoding="utf-8")

        processes, errors = deserialize_processes(processes_dir)

        assert not processes, f"Broken {type(processes.values())} file should not deserialize successfully"
        expected_key = Process.as_link(broken_process.name)
        assert expected_key in errors, (
            f"Error key '{expected_key}' missing; collected keys: {list(errors.keys())}"
        )



class TestSerializer:

    def test_serializer_round_trip_sanity_check(self, tmp_path):
        model = build_mock_model()
        serialize_model(model, str(tmp_path))
        model_deserialized, errors = deserialize_model(str(tmp_path))
        assert model.to_dict() == model_deserialized.to_dict()

        
    def test_serialize_dimensions_creates_hierarchy_and_subset_files(self, tmp_path):
        model = build_mock_model()
        serialize_model(model, str(tmp_path))

        dim_dir = tmp_path / 'dimensions'
        assert dim_dir.exists()

        dimension = model.dimensions[0]
        dim_file = dim_dir / f"{dimension.name}.json"
        hierarchy_dir = dim_dir / f"{dimension.name}.hierarchies"
        hierarchy = dimension.hierarchies[0]
        hierarchy_file = hierarchy_dir / f"{hierarchy.name}.json"
        subset_dir = hierarchy_dir / f"{hierarchy.name}.subsets"

        assert dim_file.exists(), f"Dimension file missing: {dim_file}"
        dim_json = json.loads(dim_file.read_text(encoding='utf-8'))
        assert dim_json["Name"] == dimension.name

        assert hierarchy_file.exists(), f"Hierarchy file missing: {hierarchy_file}"
        hierarchy_json = json.loads(hierarchy_file.read_text(encoding='utf-8'))
        assert hierarchy_json["Name"] == hierarchy.name
        assert hierarchy_json["Elements"], "Hierarchy elements should be serialized"

        if hierarchy.subsets:
            for subset in hierarchy.subsets:
                subset_file = subset_dir / f"{subset.name}.json"
                assert subset_file.exists(), f"Subset file missing: {subset_file}"
                subset_json = json.loads(subset_file.read_text(encoding='utf-8'))
                assert subset_json["Name"] == subset.name


    def test_serialize_processes_creates_ti_and_json(self, tmp_path):
        model = build_mock_model()
        serialize_model(model, str(tmp_path))

        process_dir = tmp_path / 'processes'
        assert process_dir.exists()

        process = model.processes[0]
        json_file = process_dir / f"{process.name}.json"
        ti_file = process_dir / f"{process.name}.ti"

        assert json_file.exists(), f"Process JSON file missing: {json_file}"
        json_data = json.loads(json_file.read_text(encoding='utf-8'))
        assert json_data["Name"] == process.name
        assert json_data["Code@Code.link"] == process.code_link

        assert ti_file.exists(), f"Process TI file missing: {ti_file}"
        assert ti_file.read_text(encoding='utf-8') == process.ti.ti_as_string()

    def test_serialize_chores_creates_json(self, tmp_path):
        model = build_mock_model(include_chore=True)
        serialize_model(model, str(tmp_path))

        chore_dir = tmp_path / 'chores'
        assert chore_dir.exists()

        chore = model.chores[0]
        chore_file = chore_dir / f"{chore.name}.json"

        assert chore_file.exists(), f"Chore JSON file missing: {chore_file}"
        chore_data = json.loads(chore_file.read_text(encoding='utf-8'))
        assert chore_data["Name"] == chore.name
        assert chore_data["Tasks"] == chore.tasks


    def test_serialize_cubes_creates_json_views_and_rules(self, tmp_path):
        model = build_mock_model(include_rules=True, additional_views=True)
        serialize_model(model, str(tmp_path))

        cube_dir = tmp_path / 'cubes'
        assert cube_dir.exists()

        cube = model.cubes[0]
        cube_json = cube_dir / f"{cube.name}.json"
        rules_file = cube_dir / f"{cube.name}.rules"
        views_dir = cube_dir / f"{cube.name}.views"

        assert cube_json.exists(), f"Cube JSON missing: {cube_json}"
        assert json.loads(cube_json.read_text(encoding='utf-8'))["Name"] == cube.name

        if cube.rules:
            assert rules_file.exists(), "Rules file should exist when cube has rules"

        assert views_dir.exists(), "Views directory missing"
        for view in cube.views:
            view_json = views_dir / f"{view.name}.json"
            view_mdx = views_dir / f"{view.name}.mdx"
            assert view_json.exists() and view_mdx.exists(), (
                f"View files missing for {view.name}: {view_json}, {view_mdx}"
            )
            assert json.loads(view_json.read_text(encoding='utf-8'))["Name"] == view.name
            assert view_mdx.read_text(encoding='utf-8') == view.mdx


    def test_serialize_handles_special_character_names(self, tmp_path):
        special_dim_name = "}Tech Dimension"
        special_hier_name = "}Tech Hierarchy"
        special_cube_name = "}Tech Cube"
        special_view_name = "View With Space"
        special_process_name = "}Tech Process"

        hierarchy = Hierarchy(
            name=special_hier_name,
            elements=[Element(name="Item 1", type="Numeric")],
            edges=[],
            subsets=[],
            source_path=f"dimensions/{special_dim_name}.hierarchies/{special_hier_name}.json"
        )
        dimension = Dimension(
            name=special_dim_name,
            hierarchies=[hierarchy],
            defaultHierarchy=hierarchy,
            source_path=f"dimensions/{special_dim_name}.json"
        )
        view = MDXView(
            name=special_view_name,
            mdx="SELECT {TM1SUBSETALL([}Tech Dimension].[}Tech Hierarchy])} ON 0 FROM [}Tech Cube]",
            source_path=f"cubes/{special_cube_name}.views/{special_view_name}.json"
        )
        cube = Cube(
            name=special_cube_name,
            dimensions=[dimension],
            rules=[],
            views=[view],
            source_path=f"cubes/{special_cube_name}.json"
        )
        ti_stub = TI("# prolog", "# metadata", "# data", "# epilog")
        process = Process(
            name=special_process_name,
            hasSecurityAccess=False,
            code_link=f"{special_process_name}.ti",
            datasource=None,
            parameters=[],
            variables=[],
            ti=ti_stub,
            source_path=f"processes/{special_process_name}.json"
        )

        special_model = Model(
            cubes=[cube],
            dimensions=[dimension],
            processes=[process],
            chores=[]
        )

        serialize_model(special_model, str(tmp_path))

        dim_path = tmp_path / "dimensions" / f"{special_dim_name}.json"
        cube_path = tmp_path / "cubes" / f"{special_cube_name}.json"
        view_json_path = tmp_path / "cubes" / f"{special_cube_name}.views" / f"{special_view_name}.json"
        process_json_path = tmp_path / "processes" / f"{special_process_name}.json"

        for path in [dim_path, cube_path, view_json_path, process_json_path]:
            assert path.exists(), f"Serialized file missing: {path}"



class TestComparator:

    mock_changeset_data = _build_mock_changeset_data()

    @staticmethod
    def _changes_by_type(changeset: Changeset, change_type: ChangeType) -> list[Change]:
        return [c for c in changeset.changes if c.change_type == change_type]

    @staticmethod
    def _bodies_by(change_set: list[Change], body_type: type) -> list:
        return [c.body for c in change_set if isinstance(c.body, body_type)]


    def test_apply_with_atomic_schema_splits_changes_and_preserves_phase_order(self, monkeypatch):
        changeset = Changeset("split_apply")
        dimension = make_dimension(name="DimAtomic", source_path="dimensions/DimAtomic")
        view = make_mdx_view(name="ViewAtomic", source_path="cubes/CubeAtomic.views/ViewAtomic.json")
        process = make_process(name="ProcRegular")
        chore = make_chore(name="ChoreRegular", task_names=["ProcRegular"])
        rule = make_rule(area="['n']", full_statement="['n']=N:1;")

        changeset.changes = [
            Change(ChangeType.ADD, ObjectType.CHORE, "chores/ChoreRegular.json", chore),
            Change(ChangeType.ADD, ObjectType.DIMENSION, "dimensions/DimAtomic.json", dimension),
            Change(ChangeType.REMOVE, ObjectType.PROCESS, "processes/ProcRegular.json", process),
            Change(ChangeType.REMOVE, ObjectType.MDX_VIEW, "cubes/CubeAtomic.views/ViewAtomic.json", view),
            Change(ChangeType.MODIFY, ObjectType.RULE, "cubes/CubeAtomic.rules", rule),
        ]

        calls: list[tuple[str, list[tuple[ChangeType, ObjectType, str]]]] = []

        def _fake_apply_atomic(filtered_changeset, tm1_service):
            calls.append((
                "atomic",
                [(c.change_type, c.object_type, c.source_path) for c in filtered_changeset.changes],
            ))
            return True

        def _fake_apply(*, changeset, tm1_service, **kwargs):
            calls.append((
                "regular",
                [(c.change_type, c.object_type, c.source_path) for c in changeset.changes],
            ))
            return True, ["regular-change"]

        monkeypatch.setattr(apply_module, "apply_atomic", _fake_apply_atomic)
        monkeypatch.setattr(apply_module, "apply", _fake_apply)

        success, applied = apply_with_atomic_schema(changeset=changeset, tm1_service=object())

        assert success is True
        assert applied == [
            "cubes/CubeAtomic.views/ViewAtomic.json",
            "dimensions/DimAtomic.json",
            "cubes/CubeAtomic.json",
            "regular-change",
        ]
        assert calls == [
            (
                "atomic",
                [
                    (ChangeType.REMOVE, ObjectType.MDX_VIEW, "cubes/CubeAtomic.views/ViewAtomic.json"),
                    (ChangeType.ADD, ObjectType.DIMENSION, "dimensions/DimAtomic.json"),
                    (ChangeType.MODIFY, ObjectType.RULE, "cubes/CubeAtomic.rules"),
                ],
            ),
            (
                "regular",
                [
                    (ChangeType.REMOVE, ObjectType.PROCESS, "processes/ProcRegular.json"),
                    (ChangeType.ADD, ObjectType.CHORE, "chores/ChoreRegular.json"),
                ],
            ),
        ]


    def test_objects_equal(self, objects_equal_data):
        obj1, obj2, shallow_fn, expect_strict_equal = objects_equal_data

        if expect_strict_equal:
            assert obj1 == obj2
        else:
            assert obj1 != obj2

        if shallow_fn:
            assert shallow_fn(obj1, obj2)


    def test_comparator_no_changes_round_trip(self, tmp_path):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        serialize_model(model=model1, dir=str(tmp_path))
        model2, error2 = deserialize_model(str(tmp_path))
        
        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        assert len(changeset.changes) == 0


    def test_comparator_has_changes_add_only(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='add_only')
        added = self._changes_by_type(changeset, ChangeType.ADD)
        modified = self._changes_by_type(changeset, ChangeType.MODIFY)
        removed = self._changes_by_type(changeset, ChangeType.REMOVE)

        assert len(added) == 6
        assert len(modified) == 5
        assert len(removed) == 0


    def test_comparator_has_changes_full(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        added = self._changes_by_type(changeset, ChangeType.ADD)
        modified = self._changes_by_type(changeset, ChangeType.MODIFY)
        removed = self._changes_by_type(changeset, ChangeType.REMOVE)

        assert len(added) == 6
        assert len(modified) == 5
        assert len(removed) == 5


    def test_comparator_dimensions_change_propagation(self):
        """Test if adding a new Subset does not propagate as a change to the Dimension object"""
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        added = self._bodies_by(self._changes_by_type(changeset, ChangeType.ADD), Subset)
        modified = self._bodies_by(self._changes_by_type(changeset, ChangeType.MODIFY), Hierarchy)

        assert (isinstance(added[0], Subset) and added[0].name == "}Temp_Subset_Discount")
        assert not modified


    def test_comparator_cubes_change_propagation(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        added = self._bodies_by(self._changes_by_type(changeset, ChangeType.ADD), MDXView)
        removed = self._bodies_by(self._changes_by_type(changeset, ChangeType.REMOVE), MDXView)
        modified = self._bodies_by(self._changes_by_type(changeset, ChangeType.MODIFY), Cube)
        modified_rule_changes = [
            c for c in self._changes_by_type(changeset, ChangeType.MODIFY)
            if c.object_type == ObjectType.RULE
        ]
        modified_rules = [c.body for c in modified_rule_changes]

        old_cube = next(c for c in model1.cubes if c.name == "testbenchSales")
        new_cube = next(c for c in model2.cubes if c.name == "testbenchSales")

        assert (isinstance(added[0], MDXView) and added[0].name == "tm1_bedrock_py_gp0vkg064lilmmga")
        assert not modified
        assert (old_cube.rules != new_cube.rules)
        assert len(modified_rules) == 1
        unified_rule = modified_rules[0]
        assert isinstance(unified_rule, Rule)
        assert unified_rule.name == "default"
        assert unified_rule.source_path == "cubes/testbenchSales.rules"
        assert unified_rule.full_statement == new_cube.get_rule_text()
        assert (isinstance(removed[0], MDXView) and removed[0].name == "tm1_bedrock_py_fp0vkg064lilmmga")

    def test_comparator_ignores_leaf_hierarchy_elements(self):
        model1 = build_mock_model()
        model2 = build_mock_model()

        leaf_hierarchy_old = Hierarchy(
            name="Leaves",
            elements=[Element(name="LeafA", type="Numeric", source_path="dimensions/MockDim.hierarchies/Leaves.json/LeafA")],
            edges=[],
            subsets=[],
            source_path="dimensions/MockDim.hierarchies/Leaves.json"
        )
        leaf_hierarchy_new = Hierarchy(
            name="Leaves",
            elements=[
                Element(name="LeafA", type="Numeric", source_path="dimensions/MockDim.hierarchies/Leaves.json/LeafA"),
                Element(name="LeafB", type="Numeric", source_path="dimensions/MockDim.hierarchies/Leaves.json/LeafB"),
            ],
            edges=[],
            subsets=[],
            source_path="dimensions/MockDim.hierarchies/Leaves.json"
        )
        model1.dimensions[0].hierarchies.append(leaf_hierarchy_old)
        model2.dimensions[0].hierarchies.append(leaf_hierarchy_new)

        changeset = Comparator().compare(model1, model2, mode='full')
        leaf_element_changes = [
            change for change in changeset.changes
            if change.object_type == ObjectType.ELEMENT and "/Leaves.json/" in change.source_path
        ]
        assert not leaf_element_changes

    def test_comparator_tracks_native_view_changes(self):
        model1 = build_mock_model()
        model2 = build_mock_model()

        model2.cubes[0].views.append(
            NativeView(
                name="DefaultNative",
                columns=[],
                rows=[],
                titles=[],
                suppress_empty_columns=True,
                suppress_empty_rows=True,
                format_string="0.#########",
                source_path="cubes/MockCube.views/DefaultNative.json",
            )
        )

        changeset = Comparator().compare(model1, model2, mode='full')
        native_adds = [
            change for change in changeset.changes
            if change.change_type == ChangeType.ADD and change.object_type == ObjectType.NATIVE_VIEW
        ]
        assert len(native_adds) == 1
        assert native_adds[0].body.name == "DefaultNative"


    def test_comparator_process_change_propagation(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        removed = self._bodies_by(self._changes_by_type(changeset, ChangeType.REMOVE), Process)
        modified = self._bodies_by(self._changes_by_type(changeset, ChangeType.MODIFY), Process)

        assert (isinstance(removed[0], Process) and removed[0].name == "Mock Process Load Product Data")
        assert (isinstance(modified[0], Process) and modified[0].name == "Mock Process Export Dimension")


    def test_comparator_chores_change_propagation(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        expected_chores = ["Mock Nightly Maintenance", "Mock Weekly Export"]

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        modified = self._bodies_by(self._changes_by_type(changeset, ChangeType.MODIFY), Chore)

        for chore_new in modified:
            assert (isinstance(chore_new, Chore) and chore_new.name in expected_chores )



class TesteExporter:

    def test_export_no_filter_rules_disables_skip_control_flags(self, mocker):
        tm1_service = mocker.Mock()
        mock_dimensions = mocker.patch("tm1_git_py.exporter.dimensions_to_model", return_value=({}, {}))
        mock_cubes = mocker.patch("tm1_git_py.exporter.cubes_to_model", return_value=({}, {}))
        mock_processes = mocker.patch("tm1_git_py.exporter.procs_to_model", return_value=({}, {}))
        mock_chores = mocker.patch("tm1_git_py.exporter.chores_to_model", return_value=({}, {}))

        model, errors = export(tm1_service, filter_rules=None)

        assert isinstance(model, Model)
        assert errors == {"dim": {}, "cube": {}, "process": {}, "chore": {}}
        mock_dimensions.assert_called_once_with(tm1_service, effective_rules=[], skip_control_dims=False)
        mock_cubes.assert_called_once_with(tm1_service, {}, effective_rules=[], skip_control_cubes=False)
        mock_processes.assert_called_once_with(tm1_service, effective_rules=[], skip_control_processes=False)
        mock_chores.assert_called_once_with(tm1_service, effective_rules=[])

    def test_export_non_technical_filter_rules_keep_skip_control_disabled(self, mocker):
        tm1_service = mocker.Mock()
        filter_rules = ["-/processes/MyProcess*"]
        mock_dimensions = mocker.patch("tm1_git_py.exporter.dimensions_to_model", return_value=({}, {}))
        mock_cubes = mocker.patch("tm1_git_py.exporter.cubes_to_model", return_value=({}, {}))
        mock_processes = mocker.patch("tm1_git_py.exporter.procs_to_model", return_value=({}, {}))
        mocker.patch("tm1_git_py.exporter.chores_to_model", return_value=({}, {}))

        export(tm1_service, filter_rules=filter_rules)

        mock_dimensions.assert_called_once_with(tm1_service, effective_rules=filter_rules, skip_control_dims=False)
        mock_cubes.assert_called_once_with(tm1_service, {}, effective_rules=filter_rules, skip_control_cubes=False)
        mock_processes.assert_called_once_with(tm1_service, effective_rules=filter_rules, skip_control_processes=False)

    def test_export_technical_intent_filter_rules_enable_skip_control_flags(self, mocker):
        tm1_service = mocker.Mock()
        filter_rules = ["-/dimensions/}*", "-/cubes/}*", "-/processes/}*"]
        mock_dimensions = mocker.patch("tm1_git_py.exporter.dimensions_to_model", return_value=({}, {}))
        mock_cubes = mocker.patch("tm1_git_py.exporter.cubes_to_model", return_value=({}, {}))
        mock_processes = mocker.patch("tm1_git_py.exporter.procs_to_model", return_value=({}, {}))
        mocker.patch("tm1_git_py.exporter.chores_to_model", return_value=({}, {}))

        export(tm1_service, filter_rules=filter_rules)

        mock_dimensions.assert_called_once_with(
            tm1_service,
            effective_rules=filter_rules,
            skip_control_dims=True,
        )
        mock_cubes.assert_called_once_with(
            tm1_service,
            {},
            effective_rules=filter_rules,
            skip_control_cubes=True,
        )
        mock_processes.assert_called_once_with(
            tm1_service,
            effective_rules=filter_rules,
            skip_control_processes=True,
        )

    def test_export_custom_filter_rules_are_forwarded_as_is(self, mocker):
        tm1_service = mocker.Mock()
        filter_rules = ["-/dimensions/TestDim1*", "-/cubes/TestCube1*"]
        mock_dimensions = mocker.patch("tm1_git_py.exporter.dimensions_to_model", return_value=({}, {}))
        mock_cubes = mocker.patch("tm1_git_py.exporter.cubes_to_model", return_value=({}, {}))
        mock_processes = mocker.patch("tm1_git_py.exporter.procs_to_model", return_value=({}, {}))
        mocker.patch("tm1_git_py.exporter.chores_to_model", return_value=({}, {}))

        export(tm1_service, filter_rules=filter_rules)

        mock_dimensions.assert_called_once_with(
            tm1_service,
            effective_rules=filter_rules,
            skip_control_dims=False,
        )
        mock_cubes.assert_called_once_with(
            tm1_service,
            {},
            effective_rules=filter_rules,
            skip_control_cubes=False,
        )
        mock_processes.assert_called_once_with(
            tm1_service,
            effective_rules=filter_rules,
            skip_control_processes=False,
        )

    def test_should_exclude_path_supports_tm1project_filter_format(self):
        filter_rules = [
            "+/data_csv/*.*",
            "-/Cubes/Views*",
            "-/dimensions/Product*",
            "-/processes/zSYS Analogic Operation Version Copy*",
        ]

        assert should_exclude_path("/cubes/viewsSales", filter_rules)
        assert should_exclude_path("DIMENSIONS/ProductHierarchy.json", filter_rules)
        assert should_exclude_path(
            "/processes/zsys analogic operation version copy.json",
            filter_rules,
        )
        assert not should_exclude_path("/cubes/SalesCube", filter_rules)



class TestChangeset:

    def test_apply_uses_sorted_order_for_delete(self, mocker):
        model_old, errors_old = deserialize_model(str(test_model_dir_base))
        model_new, errors_new = deserialize_model(str(test_model_dir_diff))
        comparator = Comparator()

        changeset = comparator.compare(model_old, model_new)

        # Patch deletes so we can inspect call order
        mock_delete = mocker.patch("tm1_git_py.apply.delete_object")
        mock_create = mocker.patch("tm1_git_py.apply.create_object")
        mock_update = mocker.patch("tm1_git_py.apply.update_object")

        # Give delete something with a .url so apply() doesn't fail
        def delete_side_effect(**kwargs):
            obj = kwargs["object_instance"]
            return types.SimpleNamespace(url=f"DELETE:{obj.__class__}:{obj.name}", status_code=200, ok=True)

        mock_delete.side_effect = delete_side_effect

        tm1_service = mocker.Mock()

        success, _ = apply(tm1_service=tm1_service, changeset=changeset, fail_fast=False)
        assert success

        # --- Assert delete order ---
        deleted_types = [
            type(call.kwargs["object_instance"])
            for call in mock_delete.call_args_list
        ]

        # For deletes, precedence is:
        # mdx_views -> rules -> cubes -> edges -> elements -> subsets -> hierarchies -> dimensions -> chore -> process
        assert deleted_types == [MDXView, Edge, Element, Chore, Process]


    def test_apply_uses_sorted_order_for_create(self, mocker):
        model_old, errors_old = deserialize_model(str(test_model_dir_base))
        model_new, errors_new = deserialize_model(str(test_model_dir_diff))
        comparator = Comparator()

        changeset = comparator.compare(model_old, model_new)

        # Patch creates so we can inspect call order
        mock_delete = mocker.patch("tm1_git_py.apply.delete_object")
        mock_create = mocker.patch("tm1_git_py.apply.create_object")
        mock_update = mocker.patch("tm1_git_py.apply.update_object")

        # Give create something with a .url so apply() doesn't fail
        def create_side_effect(**kwargs):
            obj = kwargs["object_instance"]
            return types.SimpleNamespace(url=f"CREATE:{obj.__class__}:{obj.name}", status_code=200, ok=True)

        mock_create.side_effect = create_side_effect

        tm1_service = mocker.Mock()

        success, _ = apply(tm1_service=tm1_service, changeset=changeset, fail_fast=False)
        assert success

        # --- Assert create order ---
        created_types = [
            type(call.kwargs["object_instance"])
            for call in mock_create.call_args_list
        ]

        # For creates, precedence is:
        # dimensions -> hierarchies -> subsets -> elements -> edges -> cubes -> mdx_views -> rules -> processes -> chores
        assert created_types == [Subset, Element, Element, Edge, Edge, MDXView]


    def test_apply_uses_sorted_order_for_update(self, mocker):
        model_old, errors_old = deserialize_model(str(test_model_dir_base))
        model_new, errors_new = deserialize_model(str(test_model_dir_diff))
        comparator = Comparator()

        changeset = comparator.compare(model_old, model_new)

        # Patch update so we can inspect call order
        mock_delete = mocker.patch("tm1_git_py.apply.delete_object")
        mock_create = mocker.patch("tm1_git_py.apply.create_object")
        mock_update = mocker.patch("tm1_git_py.apply.update_object")

        # Give update something with a .url so apply() doesn't fail
        def update_side_effect(**kwargs):
            obj = kwargs["object_instance"]
            return types.SimpleNamespace(url=f"UPDATE:{obj.__class__}:{obj.name}", status_code=200, ok=True)

        mock_update.side_effect = update_side_effect

        tm1_service = mocker.Mock()

        success, _ = apply(tm1_service=tm1_service, changeset=changeset, fail_fast=False)
        assert success

        # --- Assert update order ---
        updated_types = [
            type(call.kwargs["object_instance"])
            for call in mock_update.call_args_list
        ]

        # For updates, precedence is:
        # subsets -> cubes (including synthesized rule updates) -> mdx_views -> processes -> chores
        assert updated_types == [Subset, Cube, MDXView, Process, Chore]


    def test_export_persists_expected_payload(self, tmp_path):
        changes = Changeset(changeset_name="mock_changes")

        created_subset = make_subset(
            name="Subset_Create",
            expression="{[Dim_New].[Hier_New].Members}",
            dimension_name="Dim_New",
            hierarchy_name="Hier_New",
        )
        removed_view = make_mdx_view(
            name="View_To_Delete",
            mdx="SELECT FROM [Cube_One]",
            source_path="cubes/Cube_One.views/View_To_Delete.json",
        )
        new_dimension = make_dimension(
            name="Dim_Update",
            hierarchy_names=["Base", "Added"],
            source_path="/dimensions/Dim_Update",
        )

        changes.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                source_path=created_subset.source_path,
                body=created_subset,
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.DIMENSION,
                source_path=new_dimension.source_path,
                body=new_dimension,
            ),
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.MDX_VIEW,
                source_path=removed_view.source_path,
                body=removed_view,
            ),
        ]

        export_path = tmp_path / "changes.yml"
        changes.export(export_path)

        exported_payload = yaml.safe_load(export_path.read_text(encoding="utf-8"))

        expected_payload = {
            "changeset_name": "mock_changes",
            "summary": {
                "add": 1,
                "remove": 1,
                "modify": 1,
            },
            "changes": [
                {
                    "change_type": "remove",
                    "object_type": "MDXView",
                    "source_path": "cubes/Cube_One.views/View_To_Delete.json",
                    "body": {
                        "name": "View_To_Delete",
                    },
                },
                {
                    "change_type": "add",
                    "object_type": "Subset",
                    "source_path": "/dimensions/Dim_New.hierarchies/Hier_New.subsets/Subset_Create",
                    "body": {
                        "name": "Subset_Create",
                        "expression": "{[Dim_New].[Hier_New].Members}",
                    },
                },
                {
                    "change_type": "modify",
                    "object_type": "Dimension",
                    "source_path": "/dimensions/Dim_Update.json",
                    "body": {
                        "name": "Dim_Update",
                        "hierarchies": [
                            "/dimensions/Dim_Update.hierarchies/dummy.json",
                            "/dimensions/Dim_Update.hierarchies/dummy.json",
                        ],
                        "default_hierarchy": "/dimensions/Dim_Update.hierarchies/dummy.json",
                    },
                },
            ],
        }

        exported_payload_pretty = json.dumps(exported_payload, sort_keys=True, indent=2)
        expected_payload_pretty = json.dumps(expected_payload, sort_keys=True, indent=2)
        assert exported_payload_pretty == expected_payload_pretty


    def test_import_changeset(self, tmp_path):
        model_old, errors_old = deserialize_model(str(test_model_dir_base))
        model_new, errors_new = deserialize_model(str(test_model_dir_diff))
        comparator = tm1_git_py.Comparator()

        changeset_compared = comparator.compare(model_old, model_new)
        export_path = tmp_path / "changes_exported.yaml"
        changeset_compared.export(file_path=export_path)

        changeset_imported = import_changeset(
            changeset_file=str(export_path)
        )

        changeset_compared.sort()
        changeset_imported.sort()

        for expected, actual in zip(changeset_compared.changes, changeset_imported.changes):
            assert expected.change_type == actual.change_type
            assert expected.object_type == actual.object_type
            assert expected.source_path == actual.source_path
            assert expected.body.__class__ == actual.body.__class__


class TestChangesetFiltering:

    def test_filter_changeset_removes_parent_and_children_across_sections(self):
        changeset = Changeset()
        dim = make_dimension(name="MockDim", source_path="dimensions/MockDim")
        hier_new = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        hier_old = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        subset = Subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
            source_path="dimensions/MockDim.hierarchies/MockHier.subsets/SubsetA.json"
        )
        subset_mod_old = Subset(
            name="SubsetMod",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
            source_path="dimensions/MockDim.hierarchies/MockHier.subsets/SubsetMod.json"
        )
        subset_mod_new = Subset(
            name="SubsetMod",
            expression="{[MockDim].[MockHier].[E1]}",
            source_path="dimensions/MockDim.hierarchies/MockHier.subsets/SubsetMod.json"
        )
        process_obj = make_process(name="KeepProcess")

        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.DIMENSION,
                source_path=dim.source_path,
                body=dim
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.HIERARCHY,
                source_path=hier_new.source_path,
                body=hier_new
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.SUBSET,
                source_path=subset_mod_new.source_path,
                body=subset_mod_new
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                source_path=subset.source_path,
                body=subset
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                source_path=process_obj.source_path,
                body=process_obj
            ),
        ]

        filtered = filter_changeset(
            changeset,
            {
                "add": [],
                "remove": ["dimensions/MockDim.json"],
                "modify": [
                    "dimensions/MockDim.hierarchies/MockHier.json"
                ],
            },
            filter_children=True
        )

        assert [obj.body.name for obj in filtered.changes] == ["SubsetA", "KeepProcess"]



    def test_filter_changeset_keeps_parent_when_only_child_matches(self):
        changeset = Changeset()
        dim = make_dimension(name="MockDim", source_path="dimensions/MockDim")
        subset = Subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
            source_path="dimensions/MockDim.hierarchies/MockHier.subsets/SubsetA.json"
        )

        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.DIMENSION,
                source_path=dim.source_path,
                body=dim
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                source_path=subset.source_path,
                body=subset
            ),
        ]

        filtered = filter_changeset(
            changeset,
            {
                "add": ["dimensions/MockDim.hierarchies/MockHier.subsets/SubsetA.json"],
                "remove": [],
                "modify": [],
            }
        )

        filtered_adds = [c.body for c in filtered.changes if c.change_type == ChangeType.ADD]
        assert [obj.name for obj in filtered_adds] == ["MockDim"]


    def test_filter_changeset_does_not_remove_children_when_filter_children_false(self):
        changeset = Changeset()
        dim = make_dimension(name="MockDim", source_path="dimensions/MockDim")
        hier_new = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        hier_old = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        subset = Subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
            source_path="dimensions/MockDim.hierarchies/MockHier.subsets/SubsetA.json"
        )
        process_obj = make_process(name="KeepProcess")

        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.DIMENSION,
                source_path=dim.source_path,
                body=dim
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.HIERARCHY,
                source_path=hier_new.source_path,
                body=hier_new
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                source_path=subset.source_path,
                body=subset
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                source_path=process_obj.source_path,
                body=process_obj
            ),
        ]

        filtered = filter_changeset(
            changeset,
            {
                "add": [],
                "remove": ["dimensions/MockDim.json"],
                "modify": [],
            },
            filter_children=False
        )

        filtered_adds = [c.body for c in filtered.changes if c.change_type == ChangeType.ADD]
        filtered_mods = [c.body for c in filtered.changes if c.change_type == ChangeType.MODIFY]
        filtered_rems = [c.body for c in filtered.changes if c.change_type == ChangeType.REMOVE]

        assert [obj.name for obj in filtered_adds] == ["SubsetA", "KeepProcess"]
        assert len(filtered_mods) == 1
        assert [obj.name for obj in filtered_rems] == []



class TestSubsetCRUD:

    def test_create_subset_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        subset_mock = make_subset(
            name="Subset_A",
            expression="{[Dim_A].[Hier_A].Members}",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
        )

        tm1py_subset_cls = mocker.patch("tm1_git_py.model.subset.TM1py.Subset")
        tm1py_subset_instance = tm1py_subset_cls.return_value
        tm1_service.subsets.create.return_value = "create-result"

        result = subset.create_subset(tm1_service, subset_mock)

        tm1py_subset_cls.assert_called_once_with(
            subset_name="Subset_A",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
            expression="{[Dim_A].[Hier_A].Members}",
        )
        tm1_service.subsets.create.assert_called_once_with(tm1py_subset_instance)
        assert result == "create-result"

    def test_delete_subset_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        subset_mock = make_subset(
            name="Subset_Delete",
            expression="{[Dim_Del].[Hier_Del].Members}",
            dimension_name="Dim_Del",
            hierarchy_name="Hier_Del",
        )

        tm1_service.subsets.delete.return_value = "delete-result"

        result = subset.delete_subset(tm1_service, subset_mock)

        tm1_service.subsets.delete.assert_called_once_with(
            subset_name="Subset_Delete",
            dimension_name="Dim_Del",
            hierarchy_name="Hier_Del",
        )
        assert result == "delete-result"


    def test_update_subset_updates_expression_and_calls_tm1(self, mocker):
        tm1_service = mocker.Mock()

        subset_new = make_subset(
            name="Subset_A",
            expression="{[Dim_A].[Hier_A].NewMembers}",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
        )

        tm1_subset_obj = mocker.Mock()
        tm1_subset_obj.expression = "{[Dim_A].[Hier_A].OldMembers}"
        tm1_service.subsets.get.return_value = tm1_subset_obj

        tm1_service.subsets.update.return_value = "update-result"

        result = subset.update_subset(tm1_service, subset_new)

        tm1_service.subsets.get.assert_called_once_with(
            subset_name="Subset_A",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
        )

        assert tm1_subset_obj.expression == "{[Dim_A].[Hier_A].NewMembers}"
        tm1_service.subsets.update.assert_called_once_with(tm1_subset_obj)
        assert result == "update-result"



class TestEdgeCRUD:

    def test_create_edge_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        edge_obj = Edge(
            parent="Parent_A",
            name="Child_A",
            weight=1,
            source_path="/dimensions/Dim_A.hierarchies/Hier_A.json/Parent_A:Child_A"
        )
        tm1_service.elements.add_edges.return_value = "create-result"

        result = edge.create_edge(tm1_service, edge_obj)

        tm1_service.elements.add_edges.assert_called_once_with(
            "Hier_A",
            "Dim_A",
            {("Parent_A", "Child_A"): 1},
        )
        assert result == "create-result"


    def test_update_edge_fetches_hierarchy_and_updates_edge(self, mocker):
        tm1_service = mocker.Mock()
        edge_obj = Edge(
            parent="Parent_B",
            name="Child_B",
            weight=2,
            source_path="/dimensions/Dim_B.hierarchies/Hier_B.json/Parent_B:Child_B"
        )

        hierarchy_object = mocker.Mock()
        tm1_service.hierarchies.get.return_value = hierarchy_object
        tm1_service.hierarchies.update.return_value = "update-result"

        result = edge.update_edge(tm1_service, edge_obj)

        tm1_service.hierarchies.get.assert_called_once_with(
            dimension_name="Dim_B",
            hierarchy_name="Hier_B",
        )
        hierarchy_object.update_edge.assert_called_once_with(
            parent="Parent_B",
            component="Child_B",
            weight=2,
        )
        tm1_service.hierarchies.update.assert_called_once_with(hierarchy_object)
        assert result == "update-result"


    def test_delete_edge_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        edge_obj = Edge(
            parent="Parent_C",
            name="Child_C",
            weight=3,
            source_path="/dimensions/Dim_C.hierarchies/Hier_C.json/Parent_C:Child_C"
        )
        tm1_service.elements.remove_edge.return_value = "delete-result"

        result = edge.delete_edge(tm1_service, edge_obj)

        tm1_service.elements.remove_edge.assert_called_once_with(
            "Hier_C",
            "Dim_C",
            "Parent_C",
            "Child_C",
        )
        assert result == "delete-result"



class TestElementCRUD:

    def test_create_element_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        element_obj = Element(
            name="Elem_A",
            type="Numeric",
            source_path="/dimensions/Dim_A.hierarchies/Hier_A.json/Elem_A"
        )

        tm1py_element_cls = mocker.patch("tm1_git_py.model.element.TM1py.Element")
        tm1py_element_instance = tm1py_element_cls.return_value
        tm1_service.elements.create.return_value = "create-result"

        result = element.create_element(tm1_service, element_obj)

        tm1py_element_cls.assert_called_once_with(name="Elem_A", element_type="Numeric")
        tm1_service.elements.create.assert_called_once_with(
            hierarchy_name="Hier_A",
            dimension_name="Dim_A",
            element=tm1py_element_instance,
        )
        assert result == "create-result"


    def test_delete_element_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        element_obj = Element(
            name="Elem_B",
            type="String",
            source_path="/dimensions/Dim_B.hierarchies/Hier_B.json/Elem_B"
        )
        tm1_service.elements.delete.return_value = "delete-result"

        result = element.delete_element(tm1_service, element_obj)

        tm1_service.elements.delete.assert_called_once_with(
            hierarchy_name="Hier_B",
            dimension_name="Dim_B",
            element_name="Elem_B",
        )
        assert result == "delete-result"



class TestHierarchyCRUD:

    def test_create_hierarchy_does_not_create_edges_or_elements(self, mocker):
        tm1_service = mocker.Mock()

        elements = [make_element("E1"), make_element("E2")]
        hierarchy_mock = make_hierarchy(
            dimension_name="Dimension_A",
            hierarchy_name="Hierarchy_A",
            elements=elements,
            edges=[
                Edge(parent="Total", name="E1", weight=1),
                Edge(parent="Total", name="E2", weight=2),
            ],
        )

        tm1py_hierarchy_cls = mocker.patch("tm1_git_py.model.hierarchy.TM1py.Hierarchy")
        tm1py_hierarchy_obj = tm1py_hierarchy_cls.return_value

        response = mocker.Mock()
        tm1_service.hierarchies.create.return_value = response
        create_element_mock = mocker.patch("tm1_git_py.model.hierarchy.create_element")

        result = hierarchy.create_hierarchy(tm1_service, hierarchy_mock)

        # Assert: TM1py.Hierarchy constructed with correct name + dimension
        tm1py_hierarchy_cls.assert_called_once_with(
            name="Hierarchy_A",
            dimension_name="Dimension_A",
        )

        # TM1 service called to create hierarchy
        tm1_service.hierarchies.create.assert_called_once_with(tm1py_hierarchy_obj)
        assert result is response

        # create_hierarchy only creates the hierarchy itself now.
        tm1py_hierarchy_obj.add_edge.assert_not_called()
        tm1_service.elements.exists.assert_not_called()
        create_element_mock.assert_not_called()


    def test_delete_hierarchy_calls_tm1_with_correct_dimension_and_name(self, mocker):
        tm1_service = mocker.Mock()

        hierarchy_mock = make_hierarchy(
            dimension_name="Dimension_X",
            hierarchy_name="Hierarchy_Delete",
        )

        tm1_service.hierarchies.delete.return_value = "delete-result"

        result = hierarchy.delete_hierarchy(tm1_service, hierarchy_mock)

        tm1_service.hierarchies.delete.assert_called_once_with(
            dimension_name="Dimension_X",
            hierarchy_name="Hierarchy_Delete",
        )
        assert result == "delete-result"



class TestDimensionCRUD:

    def test_create_dimension_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        dimension_input = mocker.Mock()
        dimension_input.name = "TestDim"

        tm1py_dimension_cls = mocker.patch("tm1_git_py.model.dimension.TM1py.Dimension")
        tm1py_dimension_instance = tm1py_dimension_cls.return_value
        tm1_service.dimensions.create.return_value = "create-result"

        result = dimension.create_dimension(tm1_service, dimension_input.name)

        tm1py_dimension_cls.assert_called_once_with("TestDim")
        tm1_service.dimensions.create.assert_called_once_with(tm1py_dimension_instance)
        assert result == "create-result"


    def test_delete_dimension_calls_delete_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.dimensions.delete.return_value = "delete-result"
        dim = make_dimension(name="TestDim", source_path="dimensions/TestDim.json")

        result = dimension.delete_dimension(tm1_service, dim)

        tm1_service.dimensions.delete.assert_called_once_with("TestDim")
        assert result == "delete-result"



class TestMDXViewCRUD:

    def test_create_mdx_view_builds_tm1py_mdxview_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()
        mdx_view = make_mdx_view(
            name="View_A",
            mdx="SELECT FROM [Cube_A]",
        )

        cube_name = "Cube_A"
        tm1py_mdxview_cls = mocker.patch("tm1_git_py.model.mdxview.TM1py.MDXView")
        tm1py_mdxview_instance = tm1py_mdxview_cls.return_value
        tm1_service.views.create.return_value = "create-result"

        result = mdxview.create_mdxview(tm1_service, mdx_view)

        tm1py_mdxview_cls.assert_called_once_with(
            cube_name=cube_name,
            view_name="View_A",
            MDX="SELECT FROM [Cube_A]",
        )
        tm1_service.views.create.assert_called_once_with(tm1py_mdxview_instance)
        assert result == "create-result"


    def test_delete_mdx_view_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.views.delete.return_value = "delete-result"
        mdx_view = make_mdx_view(
            name="View_A",
            mdx="SELECT FROM [Cube_A]",
        )

        result = mdxview.delete_mdxview(tm1_service, mdx_view)

        tm1_service.views.delete.assert_called_once_with(view_name=mdx_view.name, cube_name="Cube_A")
        assert result == "delete-result"


    def test_update_mdx_view_updates_mdx_and_calls_update(self, mocker):
        tm1_service = mocker.Mock()
        cube_name = "Cube_A"

        mdx_view_new = make_mdx_view(
            name="View_A",
            mdx="SELECT {[Dim].[Elem]} ON 0 FROM [Cube_A]",
        )

        tm1_mdx_view_obj = mocker.Mock()
        tm1_mdx_view_obj.mdx = "OLD MDX"
        tm1_service.views.get_mdx_view.return_value = tm1_mdx_view_obj
        tm1_service.views.update.return_value = "update-result"

        result = mdxview.update_mdxview(tm1_service, mdx_view_new)

        # Assert: we got the existing MDX view from TM1
        tm1_service.views.get_mdx_view.assert_called_once_with(
            cube_name=cube_name,
            view_name="View_A",
        )

        # The MDX on the TM1 object should be updated to the new MDX
        assert tm1_mdx_view_obj.mdx == "SELECT {[Dim].[Elem]} ON 0 FROM [Cube_A]"

        # And update() should be called with that object
        tm1_service.views.update.assert_called_once_with(tm1_mdx_view_obj)

        # Function returns whatever TM1 update() returned
        assert result == "update-result"



class TestCubeCRUD:

    def test_create_cube_builds_tm1py_cube_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()
        cube_mock = make_cube(
            name="Cube_A",
            dimension_names=["Version", "Period", "Channel"],
        )

        tm1py_cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        tm1py_cube_instance = tm1py_cube_cls.return_value
        tm1_service.cubes.create.return_value = "create-result"

        result = cube.create_cube(tm1_service, cube_mock)

        expected_dims = ["Version", "Period", "Channel"]
        expected_rule_text = cube_mock.get_rule_text()

        tm1py_cube_cls.assert_called_once_with(
            cube_mock.name,
            expected_dims,
            expected_rule_text,
        )
        tm1_service.cubes.create.assert_called_once_with(tm1py_cube_instance)
        assert result == "create-result"


    def test_delete_cube_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.cubes.delete.return_value = "delete-result"
        cube_name = "Cube_To_Delete"
        cube_obj = make_cube(name=cube_name)

        result = cube.delete_cube(tm1_service, cube_obj)

        tm1_service.cubes.delete.assert_called_once_with(cube_name)
        assert result == "delete-result"


    @pytest.mark.skip
    def test_update_cube_updates_rules_when_views_same(self, mocker):
        tm1_service = mocker.Mock()

        dim_names = ["Version", "Period"]

        view = MDXView(
            name="ViewSame",
            mdx="SELECT FROM [Cube_B]",
            source_path="/views/Cube_B/ViewSame.json",
        )
        views = [view]

        rules_old = [
            make_rule(
                area="['n']",
                full_statement="['n'] = N: 1;",
                comment="// old",
            )
        ]
        rules_new = [
            make_rule(
                area="['n']",
                full_statement="['n'] = N: 2;",
                comment="// new",
            )
        ]

        cube_old = make_cube("Cube_B", dim_names, rules_old, views)
        cube_new = make_cube("Cube_B", dim_names, rules_new, views)

        payload = {"old": cube_old, "new": cube_new}

        class RulesObj:
            def __init__(self, body: str):
                self.body = body
                self._text = body

        cube_obj = mocker.Mock()
        cube_obj.rules = RulesObj(body="some different rules")
        tm1_service.cubes.get.return_value = cube_obj

        tm1_service.cubes.update.return_value = "update-result"

        # ACT
        result = cube.update_cube(tm1_service, payload)

        # Rules updated
        new_rule_text = cube_new.get_rule_text()
        assert cube_obj.rules._text == new_rule_text

        tm1_service.cubes.update.assert_called_once_with(cube_obj)
        assert result == "update-result"


    @pytest.mark.skip
    def test_update_cube_reorders_dimensions_when_order_changes_only(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Cube_Order", ["A", "B", "C"])
        cube_new = make_cube("Cube_Order", ["B", "C", "A"])

        payload = {"old": cube_old, "new": cube_new}

        class RulesObj:
            def __init__(self, body: str):
                self.body = body
                self._text = body

        cube_obj = mocker.Mock()
        cube_obj.rules = RulesObj(body="")
        tm1_service.cubes.get.return_value = cube_obj

        tm1_service.cubes.update.return_value = "update-result"

        result = cube.update_cube(tm1_service, payload)

        # --- Assertions on dimension reordering logic ---
        tm1_service.cubes.get.assert_called_once_with("Cube_Order")

        # Because order changed but set is the same, we must reorder storage dims
        tm1_service.cubes.update_storage_dimension_order.assert_called_once_with(
            cube_name="Cube_Order",
            dimension_names=["B", "C", "A"],
        )

        # Rules should not change (both empty), so no extra logic beyond update()
        tm1_service.cubes.update.assert_called_once_with(cube_obj)
        assert result == "update-result"


    @pytest.mark.skip
    def test_add_dimension_to_cube_uses_first_leaf_and_copies_via_temp_cube(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year"]
        dims_new = ["Version", "Year", "Region"]

        # --- TM1 mocks ---

        # Hierarchy with one consolidated + one leaf
        hier = mocker.Mock()
        consolidated = mocker.Mock()
        consolidated.name = "Total"
        consolidated.element_type = "Consolidated"
        leaf = mocker.Mock()
        leaf.name = "Leaf1"
        leaf.element_type = "Numeric"
        hier.elements.values.return_value = [consolidated, leaf]
        tm1_service.hierarchies.get.return_value = hier

        # Patch TM1py.Cube
        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")

        # Patch bedrock copy
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        # Patch create/delete cube wrappers
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        # create_dimension / element.create_element should NOT be called here
        create_dimension_mock = mocker.patch("tm1_git_py.model.cube.create_dimension")
        create_elem_mock = mocker.patch("tm1_git_py.model.cube.element.create_element")

        # --- ACT ---
        cube._add_dimensions_to_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
        )

        temp_cube_name = "Sales__tmp_add_dims"

        # 1) default element: first leaf, no new element created
        tm1_service.hierarchies.get.assert_called_once_with(
            dimension_name="Region",
            hierarchy_name="Region",
        )
        create_dimension_mock.assert_not_called()
        create_elem_mock.assert_not_called()

        # 2) temp cube creation
        cube_cls.assert_called_once_with(
            name=temp_cube_name,
            dimensions=dims_new,
            rules="",
        )
        tm1_service.cubes.create.assert_called_once_with(cube_cls.return_value)

        # 2) first data_copy_intercube: old -> temp with target_dim_mapping
        assert copy_mock.call_count == 2
        first_call = copy_mock.call_args_list[0]
        first_kwargs = first_call.kwargs

        assert first_kwargs["tm1_service"] is tm1_service
        assert first_kwargs["target_cube_name"] == temp_cube_name
        assert first_kwargs["target_dim_mapping"] == {"Region": "Leaf1"}
        assert first_kwargs["clear_target"] is True
        mdx1 = first_kwargs["data_mdx"]
        assert "[Sales]" in mdx1
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "TM1SUBSETALL([Year])" in mdx1
        assert "Region" not in mdx1

        # 3) original cube deleted, cube recreated with new definition
        delete_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube_name="Sales",
        )
        create_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube=cube_new,
        )

        # 4) second data_copy_intercube: temp -> final (no target_dim_mapping)
        second_call = copy_mock.call_args_list[1]
        second_kwargs = second_call.kwargs

        assert second_kwargs["tm1_service"] is tm1_service
        assert second_kwargs["target_cube_name"] == "Sales"
        assert second_kwargs["clear_target"] is True
        assert "target_dim_mapping" not in second_kwargs
        mdx2 = second_kwargs["data_mdx"]
        assert "[Sales__tmp_add_dims]" in mdx2
        assert "TM1SUBSETALL([Version])" in mdx2
        assert "TM1SUBSETALL([Year])" in mdx2
        assert "TM1SUBSETALL([Region])" in mdx2

        # 5) temp cube deletion
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.skip(reason="Ignored per user request")
    def test_add_dimension_to_cube_creates_default_leaf_when_no_leaf_exists(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version"]
        dims_new = ["Version", "NewDim"]

        # Hierarchy with only consolidated elements (no leaves)
        hier = mocker.Mock()
        cons = mocker.Mock()
        cons.name = "Total"
        cons.element_type = "Consolidated"
        hier.elements.values.return_value = [cons]
        tm1_service.hierarchies.get.return_value = hier

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        create_dimension_mock = mocker.patch("tm1_git_py.model.cube.create_dimension")
        create_elem_mock = mocker.patch("tm1_git_py.model.cube.element.create_element")

        cube._add_dimensions_to_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
        )

        temp_cube_name = "Sales__tmp_add_dims"

        # 1) dimension created, then hierarchy default element created
        create_dimension_mock.assert_called_once_with(
            tm1_service=tm1_service,
            dimension="NewDim",
        )

        tm1_service.hierarchies.get.assert_called_with(
            dimension_name="NewDim",
            hierarchy_name="NewDim",
        )

        create_elem_mock.assert_called_once()
        elem_kwargs = create_elem_mock.call_args.kwargs
        elem_attributes = elem_kwargs["element"].body_as_dict
        assert elem_kwargs["dimension_name"] == "NewDim"
        assert elem_kwargs["hierarchy_name"] == "NewDim"
        assert elem_kwargs["element"].name == "Legacy Data"
        assert elem_attributes["Type"] == "Numeric"

        # hierarchy should be updated with the new element
        hier.add_element.assert_called_once_with(
            element_name="Legacy Data",
            element_type="Numeric",
        )
        tm1_service.hierarchies.update.assert_called_once_with(hierarchy=hier)

        # 2) temp cube created with new dimensions
        assert cube_cls.call_count == 2

        # First call should be for the temp cube, using keyword args
        temp_call = cube_cls.call_args_list[0]
        assert temp_call.kwargs == {
            "name": temp_cube_name,
            "dimensions": dims_new,
            "rules": "",
        }

        # 3) first copy uses 'Legacy Data' as target_dim_mapping
        first_kwargs = copy_mock.call_args_list[0].kwargs
        assert first_kwargs["target_dim_mapping"] == {"NewDim": "Legacy Data"}


    @pytest.mark.skip
    def test_add_dimension_to_cube_raises_on_cube_name_mismatch(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales_Old")
        cube_new = make_cube("Sales_New")

        with pytest.raises(ValueError) as excinfo:
            cube._add_dimensions_to_cube(
                tm1_service=tm1_service,
                cube_old=cube_old,
                cube_new=cube_new,
                dims_old=["Version"],
                dims_new=["Version", "Region"],
            )

        assert "Cube name mismatch" in str(excinfo.value)

        tm1_service.cubes.create.assert_not_called()


    @pytest.mark.skip
    def test_delete_dimensions_sum_all_default_strategy(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year", "Region"]
        dims_new = ["Version", "Year"]

        # TM1: temp cube does not exist yet
        tm1_service.cubes.exists.return_value = False

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
            strategies=None,
            default_strategy="sum_all",
        )

        temp_cube_name = "Sales__tmp_del_multi"

        # 1) temp cube created with reduced dims
        assert cube_cls.call_count >= 1
        first_cube_call = cube_cls.call_args_list[0]
        assert first_cube_call.kwargs == {
            "name": temp_cube_name,
            "dimensions": dims_new,
            "rules": "",
        }
        tm1_service.cubes.create.assert_called_once_with(cube_cls.return_value)

        # 2) first data_copy_intercube: old -> temp
        assert copy_mock.call_count == 2
        first_call_kwargs = copy_mock.call_args_list[0].kwargs

        assert first_call_kwargs["tm1_service"] is tm1_service
        assert first_call_kwargs["target_cube_name"] == temp_cube_name
        # sum_all => no explicit source_dim_mapping
        assert first_call_kwargs.get("source_dim_mapping") is None
        assert first_call_kwargs["clear_target"] is True
        assert first_call_kwargs["sum_numeric_duplicates"] is True

        mdx1 = first_call_kwargs["data_mdx"]
        # All deleted dims use TM1SUBSETALL
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "TM1SUBSETALL([Year])" in mdx1
        assert "TM1SUBSETALL([Region])" in mdx1
        assert "FILTER(" not in mdx1  # no keep_by_attr filters here

        # 3) original cube deleted & recreated
        delete_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube_name="Sales",
        )
        create_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube=cube_new,
        )

        # 4) second data_copy_intercube: temp -> final
        second_call_kwargs = copy_mock.call_args_list[1].kwargs
        assert second_call_kwargs["target_cube_name"] == "Sales"
        assert second_call_kwargs["clear_target"] is True
        assert second_call_kwargs["sum_numeric_duplicates"] is True
        mdx2 = second_call_kwargs["data_mdx"]
        # Now only new dims appear
        assert "TM1SUBSETALL([Version])" in mdx2
        assert "TM1SUBSETALL([Year])" in mdx2
        assert "Region" not in mdx2

        # 5) temp cube deleted at the end
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.skip
    def test_delete_dimensions_keep_element_strategy(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year"]
        dims_new = ["Year"]

        strategies = {
            "Version": {
                "strategy": "keep_element",
                "element": "Actual",
            }
        }

        tm1_service.cubes.exists.return_value = False

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
            strategies=strategies,
            default_strategy="sum_all",
        )

        temp_cube_name = "Sales__tmp_del_multi"

        # temp cube created as before
        cube_cls.assert_called()
        tm1_service.cubes.create.assert_called_once()

        # first bedrock call: old -> temp
        first_kwargs = copy_mock.call_args_list[0].kwargs
        assert first_kwargs["target_cube_name"] == temp_cube_name
        # keep_element => using source_dim_mapping for Version
        assert first_kwargs["source_dim_mapping"] == {"Version": "Actual"}
        assert first_kwargs["sum_numeric_duplicates"] is True

        # MDX still uses TM1SUBSETALL for Version; filtering is handled by source_dim_mapping
        mdx1 = first_kwargs["data_mdx"]
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "FILTER(" not in mdx1

        # clean-up flow same as sum_all
        delete_cube_mock.assert_called_once()
        create_cube_mock.assert_called_once()
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.skip
    def test_delete_dimensions_keep_element_requires_element(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")
        dims_old = ["Version"]
        dims_new = []

        strategies = {
            "Version": {
                "strategy": "keep_element",
                # 'element' missing on purpose
            }
        }

        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        with pytest.raises(ValueError) as excinfo:
            cube._delete_dimensions_from_cube(
                tm1_service=tm1_service,
                cube_old=cube_old,
                cube_new=cube_new,
                dims_old=dims_old,
                dims_new=dims_new,
                strategies=strategies,
            )

        assert "requires an 'element' key" in str(excinfo.value)
        # Must not call bedrock if config is invalid
        copy_mock.assert_not_called()


    @pytest.mark.skip
    def test_delete_dimensions_keep_by_attr_strategy(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Region", "Year"]
        dims_new = ["Version", "Year"]

        strategies = {
            "Region": {
                "strategy": "keep_by_attr",
                "attr_name": "KeepOnDrop",
                "attr_value": "Y",
            }
        }

        tm1_service.cubes.exists.return_value = False

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
            strategies=strategies,
            default_strategy="sum_all",
        )

        temp_cube_name = "Sales__tmp_del_multi"
        cube_cls.assert_called()
        tm1_service.cubes.create.assert_called_once()

        first_kwargs = copy_mock.call_args_list[0].kwargs
        assert first_kwargs["target_cube_name"] == temp_cube_name
        # keep_by_attr => no source_dim_mapping
        assert first_kwargs.get("source_dim_mapping") is None

        mdx1 = first_kwargs["data_mdx"]
        # Version & Year are standard TM1SUBSETALL
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "TM1SUBSETALL([Year])" in mdx1

        # Region uses FILTER with attribute logic
        assert "FILTER(" in mdx1
        assert "TM1SUBSETALL([Region])" in mdx1
        assert '[Region].CURRENTMEMBER.PROPERTIES("KeepOnDrop")' in mdx1
        assert '= "Y"' in mdx1

        delete_cube_mock.assert_called_once()
        create_cube_mock.assert_called_once()
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.parametrize("bad_cfg", [
        {"strategy": "keep_by_attr", "attr_name": "KeepOnDrop"},  # no attr_value
        {"strategy": "keep_by_attr", "attr_value": "Y"},  # no attr_name
        {"strategy": "keep_by_attr"},  # both missing
    ])
    @pytest.mark.skip
    def test_delete_dimensions_keep_by_attr_requires_attr_name_and_value(self, mocker, bad_cfg):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")
        dims_old = ["Region"]
        dims_new = []  # delete Region

        strategies = {"Region": bad_cfg}

        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        with pytest.raises(ValueError) as excinfo:
            cube._delete_dimensions_from_cube(
                tm1_service=tm1_service,
                cube_old=cube_old,
                cube_new=cube_new,
                dims_old=dims_old,
                dims_new=dims_new,
                strategies=strategies,
            )

        assert "requires 'attr_name' and 'attr_value'" in str(excinfo.value)
        copy_mock.assert_not_called()


    @pytest.mark.skip
    def test_delete_dimensions_no_deleted_dims_returns_early(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year"]
        dims_new = ["Version", "Year"]

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
        )

        cube_cls.assert_not_called()
        copy_mock.assert_not_called()
        delete_cube_mock.assert_not_called()
        create_cube_mock.assert_not_called()
        tm1_service.cubes.exists.assert_not_called()



class TestProcessCRUD:

    def test_create_process_builds_tm1py_process_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()

        process_mock = make_process(
            name="Proc_A",
            has_security_access=True,
            datasource_type="None",
        )

        tm1py_process_cls = mocker.patch("tm1_git_py.model.process.TM1py.Process")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.create.return_value = "create-result"

        result = process.create_process(tm1_service, process_mock)

        tm1py_process_cls.assert_called_once_with(
            name="Proc_A",
            has_security_access=True,
            datasource_type="None",
            parameters=process_mock.parameters,
            variables=process_mock.variables,
        )

        tm1_service.processes.create.assert_called_once_with(tm1py_process_instance)
        assert result == "create-result"


    def test_delete_process_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.processes.delete.return_value = "delete-result"
        proc = make_process(name="Proc_To_Delete")

        result = process.delete_process(tm1_service, proc)

        tm1_service.processes.delete.assert_called_once_with("Proc_To_Delete")
        assert result == "delete-result"


    def test_update_process_updates_core_fields_without_param_var_changes(self, mocker):
        tm1_service = mocker.Mock()

        process_new = make_process(
            name="Proc_A",
            has_security_access=True,
            datasource_type="ODBC"
        )

        tm1_process_obj = mocker.Mock()
        # Mock the parameter / variable collections coming from the live TM1 object.
        tm1_process_obj.parameters = list(process_new.parameters)
        tm1_process_obj.variables = list(process_new.variables)
        tm1_service.processes.get.return_value = tm1_process_obj
        tm1_service.processes.update.return_value = "update-result"

        # Act
        result = process.update_process(tm1_service, process_new)

        tm1_service.processes.get.assert_called_once_with(name_process="Proc_A")

        # Core fields updated
        assert tm1_process_obj.datasource_type == "ODBC"
        assert tm1_process_obj.has_security_access is True

        # No parameter/variable modifications because lists are identical
        tm1_process_obj.add_parameter.assert_not_called()
        tm1_process_obj.remove_parameter.assert_not_called()
        tm1_process_obj.add_variable.assert_not_called()
        tm1_process_obj.remove_variable.assert_not_called()

        # Update call + propagated result
        tm1_service.processes.update.assert_called_once_with(tm1_process_obj)
        assert result == "update-result"


    def test_update_process_adds_and_removes_parameters_and_variables(self, mocker):
        tm1_service = mocker.Mock()

        params_new = [
            {"name": "p1", "prompt": "P1", "value": "1", "type": "Numeric"},
            {"name": "p3", "prompt": "P3", "value": "3", "type": "String"},
        ]
        vars_new = [
            {"name": "v1", "type": "String"},
            {"name": "v3", "type": "String"},
        ]

        process_new = make_process(
            name="Proc_B",
            has_security_access=False,
            datasource_type="None",
            parameters=params_new,
            variables=vars_new,
        )

        params_old = [
            {"name": "p1", "prompt": "P1", "value": "1", "type": "Numeric"},
            {"name": "p2", "prompt": "P2", "value": "2", "type": "String"},
        ]
        vars_old = [
            {"name": "v1", "type": "String"},
            {"name": "v2", "type": "String"},
        ]

        tm1_process_obj = mocker.Mock()
        # Mock the parameter / variable collections fetched from the live TM1 process.
        tm1_process_obj.parameters = list(params_old)
        tm1_process_obj.variables = list(vars_old)
        tm1_service.processes.get.return_value = tm1_process_obj

        update_result = mocker.sentinel.update_result
        tm1_service.processes.update.return_value = update_result

        # Act
        result = process.update_process(tm1_service, process_new)

        # Check add/remove for parameters
        tm1_process_obj.add_parameter.assert_called_once_with(
            name="p3",
            prompt="P3",
            value="3",
            parameter_type="String",
        )
        tm1_process_obj.remove_parameter.assert_called_once_with(name="p2")

        # Check add/remove for variables
        tm1_process_obj.add_variable.assert_called_once_with(
            name="v3",
            variable_type="String",
        )
        tm1_process_obj.remove_variable.assert_called_once_with(name="v2")

        # Ensure update was still called with the process object
        tm1_service.processes.update.assert_called_once_with(tm1_process_obj)
        assert result is update_result

    def test_create_process_accepts_datasource_dict(self, mocker):
        tm1_service = mocker.Mock()
        process_mock = make_process(name="Proc_DictDS", datasource_type={"type": "None"})

        tm1py_process_cls = mocker.patch("tm1_git_py.model.process.TM1py.Process")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.create.return_value = "create-result"

        result = process.create_process(tm1_service, process_mock)

        tm1py_process_cls.assert_called_once_with(
            name="Proc_DictDS",
            has_security_access=process_mock.hasSecurityAccess,
            datasource_type="None",
            parameters=process_mock.parameters,
            variables=process_mock.variables,
        )
        tm1_service.processes.create.assert_called_once_with(tm1py_process_instance)
        assert result == "create-result"

    def test_update_process_normalizes_empty_datasource_to_none(self, mocker):
        tm1_service = mocker.Mock()
        process_new = make_process(name="Proc_EmptyDS", datasource_type="")

        tm1_process_obj = mocker.Mock()
        tm1_process_obj.parameters = list(process_new.parameters)
        tm1_process_obj.variables = list(process_new.variables)
        tm1_service.processes.get.return_value = tm1_process_obj
        tm1_service.processes.update.return_value = "update-result"

        result = process.update_process(tm1_service, process_new)

        assert tm1_process_obj.datasource_type == "None"
        tm1_service.processes.update.assert_called_once_with(tm1_process_obj)
        assert result == "update-result"



class TestChoreCRUD:

    def test_create_chore_builds_tm1py_chore_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()

        chore_mock = make_chore(
            name="Chore_A",
            start_time="2025-04-22T10:07:00+01:00",
            dst_sensitive=True,
            active=False,
            execution_mode="SingleCommit",
            frequency="P01DT00H00M00S",
            task_names=["Proc1", "Proc2"],
        )

        create_chore_task_mock = mocker.patch(
            "tm1_git_py.model.chore.create_chore_task"
        )
        chore_task_instances = [
            mocker.Mock(name="ChoreTask0"),
            mocker.Mock(name="ChoreTask1"),
        ]
        create_chore_task_mock.side_effect = chore_task_instances

        start_time_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreStartTime.from_string",
            return_value="parsed-start-time",
        )
        frequency_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreFrequency.from_string",
            return_value="parsed-frequency",
        )

        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value
        tm1_service.chores.create.return_value = "create-result"

        result = chore.create_chore(tm1_service, chore_mock)

        assert create_chore_task_mock.call_count == 2
        create_chore_task_mock.assert_any_call(task=chore_mock.tasks[0], step=0)
        create_chore_task_mock.assert_any_call(task=chore_mock.tasks[1], step=1)

        start_time_from_string.assert_called_once_with(chore_mock.start_time)
        frequency_from_string.assert_called_once_with(chore_mock.frequency)

        tm1py_chore_cls.assert_called_once_with(
            name="Chore_A",
            start_time="parsed-start-time",
            dst_sensitivity=True,
            active=False,
            execution_mode="SingleCommit",
            frequency="parsed-frequency",
            tasks=chore_task_instances,
        )

        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"


    def test_delete_chore_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.chores.delete.return_value = "delete-result"
        chore_obj = make_chore(name="Chore_To_Delete")

        result = chore.delete_chore(tm1_service, chore_obj)

        tm1_service.chores.delete.assert_called_once_with("Chore_To_Delete")
        assert result == "delete-result"


    def test_update_chore_updates_fields_and_tasks_when_exists(self, mocker):
        tm1_service = mocker.Mock()

        chore_new = make_chore(
            name="Chore_A",
            start_time="2025-04-23T10:00:00+01:00",
            dst_sensitive=False,
            active=True,
            execution_mode="MultipleCommit",
            frequency="P02DT00H00M00S",
            task_names=["Proc1_new", "Proc2_new"],
        )

        tm1_service.chores.create.return_value = "create-result"
        create_chore_task_mock = mocker.patch(
            "tm1_git_py.model.chore.create_chore_task"
        )
        chore_task_instances = [
            mocker.Mock(name="ChoreTask0_new"),
            mocker.Mock(name="ChoreTask1_new"),
        ]
        create_chore_task_mock.side_effect = chore_task_instances

        start_time_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreStartTime.from_string",
            return_value="parsed-start-time",
        )
        frequency_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreFrequency.from_string",
            return_value="parsed-frequency",
        )
        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value

        result = chore.update_chore(tm1_service, chore_new)

        assert create_chore_task_mock.call_count == 2
        create_chore_task_mock.assert_any_call(task=chore_new.tasks[0], step=0)
        create_chore_task_mock.assert_any_call(task=chore_new.tasks[1], step=1)

        start_time_from_string.assert_called_once_with(chore_new.start_time)
        frequency_from_string.assert_called_once_with(chore_new.frequency)
        tm1_service.chores.delete.assert_called_once_with("Chore_A")
        tm1py_chore_cls.assert_called_once_with(
            name="Chore_A",
            start_time="parsed-start-time",
            dst_sensitivity=False,
            active=True,
            execution_mode="MultipleCommit",
            frequency="parsed-frequency",
            tasks=chore_task_instances,
        )
        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"


    def test_update_chore_activates_when_active_flag_changes_from_false_to_true(self, mocker):
        tm1_service = mocker.Mock()

        chore_new = make_chore(
            name="Chore_B",
            active=True,
            task_names=["ProcX"],
        )

        tm1_service.chores.create.return_value = "create-result"

        mocker.patch("tm1_git_py.model.chore.create_chore_task", return_value=mocker.Mock())
        mocker.patch("tm1_git_py.model.chore.ChoreStartTime.from_string", return_value="parsed-start-time")
        mocker.patch("tm1_git_py.model.chore.ChoreFrequency.from_string", return_value="parsed-frequency")
        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value

        result = chore.update_chore(tm1_service, chore_new)

        tm1_service.chores.delete.assert_called_once_with("Chore_B")
        tm1py_chore_cls.assert_called_once()
        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"

    def test_update_chore_accepts_date_only_start_time(self, mocker):
        tm1_service = mocker.Mock()

        chore_new = make_chore(
            name="Chore_DateOnly",
            start_time="2026-03-05",
            task_names=["ProcX"],
        )

        tm1_service.chores.create.return_value = "create-result"

        mocker.patch("tm1_git_py.model.chore.create_chore_task", return_value=mocker.Mock())
        start_time_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreStartTime.from_string",
            return_value="parsed-start-time"
        )
        mocker.patch(
            "tm1_git_py.model.chore.ChoreFrequency.from_string",
            return_value="parsed-frequency"
        )
        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value

        result = chore.update_chore(tm1_service, chore_new)

        start_time_from_string.assert_called_once_with("2026-03-05T00:00:00+00:00")
        tm1_service.chores.delete.assert_called_once_with("Chore_DateOnly")
        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"


class TestTITranspilerPhase1:

    @staticmethod
    def _ti(*lines: str) -> str:
        return "\r\n".join(lines)

    @staticmethod
    def _rule_cube(rule_text: str) -> Cube:
        rules = [Rule(area="[A]", full_statement=rule_text)] if rule_text else []
        return Cube(
            name="RuleCube",
            dimensions=[],
            rules=rules,
            views=[],
            source_path="/cubes/RuleCube.json",
        )

    @pytest.mark.parametrize(
        ("actual", "expected"),
        [
            pytest.param(
                lambda: dimension.build_dimension_create_ti(
                    Dimension(name="O'Neill", hierarchies=[], defaultHierarchy=None, source_path="/dimensions/O_Neill.json")
                ),
                _ti(
                    "# --- Create Dimension: O''Neill ---",
                    "IF( DimensionExists('O''Neill') = 0 );",
                    "    DimensionCreate('O''Neill');",
                    "ENDIF;",
                ),
                id="dimension-create",
            ),
            pytest.param(
                lambda: dimension.build_dimension_update_ti(
                    Dimension(name="O'Neill", hierarchies=[], defaultHierarchy=None, source_path="/dimensions/O_Neill.json")
                ),
                _ti("# --- Update Dimension: O''Neill ---"),
                id="dimension-update",
            ),
            pytest.param(
                lambda: dimension.build_dimension_delete_ti(
                    Dimension(name="O'Neill", hierarchies=[], defaultHierarchy=None, source_path="/dimensions/O_Neill.json")
                ),
                _ti(
                    "# --- Delete Dimension: O''Neill ---",
                    "IF( DimensionExists('O''Neill') = 1 );",
                    "    DimensionDestroy('O''Neill');",
                    "ENDIF;",
                ),
                id="dimension-delete",
            ),
            pytest.param(
                lambda: hierarchy.build_hierarchy_create_ti(
                    Hierarchy(
                        name="Hier'O",
                        elements=[],
                        edges=[],
                        subsets=[],
                        source_path="/dimensions/Dim.hierarchies/HierO.json",
                    )
                ),
                _ti(
                    "# --- Create Hierarchy: Dim:Hier''O ---",
                    "IF( HierarchyExists('Dim', 'Hier''O') = 0 );",
                    "   HierarchyCreate('Dim', 'Hier''O');",
                    "ENDIF;",
                ),
                id="hierarchy-create",
            ),
            pytest.param(
                lambda: hierarchy.build_hierarchy_update_ti(
                    Hierarchy(
                        name="Hier'O",
                        elements=[],
                        edges=[],
                        subsets=[],
                        source_path="/dimensions/Dim.hierarchies/HierO.json",
                    )
                ),
                _ti("# --- Update Hierarchy: Hier''O ---"),
                id="hierarchy-update",
            ),
            pytest.param(
                lambda: hierarchy.build_hierarchy_delete_ti(
                    Hierarchy(
                        name="Hier'O",
                        elements=[],
                        edges=[],
                        subsets=[],
                        source_path="/dimensions/Dim.hierarchies/HierO.json",
                    )
                ),
                _ti(
                    "# --- Delete Hierarchy: Hier''O ---",
                    "IF( HierarchyExists('Dim', 'Hier''O') = 1 );",
                    "   HierarchyDestroy('Dim', 'Hier''O');",
                    "ENDIF;",
                ),
                id="hierarchy-delete",
            ),
            pytest.param(
                lambda: element.build_element_create_ti(
                    Element(
                        name="El'O",
                        type="String",
                        source_path="dimensions/Dim.hierarchies/Hier.json/ElO",
                    )
                ),
                _ti(
                    "# --- Create Element: El''O ---",
                    "IF( HierarchyElementExists('Dim', 'Hier', 'El''O') = 0 );",
                    "    HierarchyElementInsert('Dim', 'Hier', '', 'El''O', 'S');",
                    "ENDIF;",
                ),
                id="element-create",
            ),
            pytest.param(
                lambda: element.build_element_update_ti(
                    Element(
                        name="El'O",
                        type="String",
                        source_path="dimensions/Dim.hierarchies/Hier.json/ElO",
                    )
                ),
                _ti(
                    "# --- Update (Recreate) Element: 'El''O' ---",
                    "# --- Delete Element: El''O ---",
                    "IF( HierarchyElementExists('Dim', 'Hier', 'El''O') = 1 );",
                    "   HierarchyElementDelete('Dim', 'Hier', 'El''O');",
                    "ENDIF;",
                    "# --- Create Element: El''O ---",
                    "IF( HierarchyElementExists('Dim', 'Hier', 'El''O') = 0 );",
                    "    HierarchyElementInsert('Dim', 'Hier', '', 'El''O', 'S');",
                    "ENDIF;",
                ),
                id="element-update",
            ),
            pytest.param(
                lambda: element.build_element_delete_ti(
                    Element(
                        name="El'O",
                        type="String",
                        source_path="dimensions/Dim.hierarchies/Hier.json/ElO",
                    )
                ),
                _ti(
                    "# --- Delete Element: El''O ---",
                    "IF( HierarchyElementExists('Dim', 'Hier', 'El''O') = 1 );",
                    "   HierarchyElementDelete('Dim', 'Hier', 'El''O');",
                    "ENDIF;",
                ),
                id="element-delete",
            ),
            pytest.param(
                lambda: edge.build_edge_create_ti(
                    Edge(
                        parent="Parent'O",
                        name="Child'O",
                        weight=1.5,
                        source_path="dimensions/Dim.hierarchies/Hier.json/Parent:Child",
                    )
                ),
                _ti(
                    "# --- Create Edge: Parent''O -> Child''O (Weight: 1.5) ---",
                    "IF( ElementIsComponent('Dim', 'Hier', 'Child''O', 'Parent''O') = 0 );",
                    "    HierarchyElementComponentAdd('Dim', 'Hier', 'Parent''O', 'Child''O', 1.5);",
                    "ENDIF;",
                ),
                id="edge-create",
            ),
            pytest.param(
                lambda: edge.build_edge_update_ti(
                    Edge(
                        parent="Parent'O",
                        name="Child'O",
                        weight=1.5,
                        source_path="dimensions/Dim.hierarchies/Hier.json/Parent:Child",
                    )
                ),
                _ti(
                    "# --- Update (Recreate) Edge: Parent''O -> Child''O ---",
                    "# --- Remove Edge: Parent''O -> Child''O ---",
                    "IF( ElementIsComponent('Dim', 'Hier', 'Child''O', 'Parent''O') = 1 );",
                    "    HierarchyElementComponentDelete('Dim', 'Hier', 'Parent''O', 'Child''O');",
                    "ENDIF;",
                    "# --- Create Edge: Parent''O -> Child''O (Weight: 1.5) ---",
                    "IF( ElementIsComponent('Dim', 'Hier', 'Child''O', 'Parent''O') = 0 );",
                    "    HierarchyElementComponentAdd('Dim', 'Hier', 'Parent''O', 'Child''O', 1.5);",
                    "ENDIF;",
                ),
                id="edge-update",
            ),
            pytest.param(
                lambda: edge.build_edge_delete_ti(
                    Edge(
                        parent="Parent'O",
                        name="Child'O",
                        weight=1.5,
                        source_path="dimensions/Dim.hierarchies/Hier.json/Parent:Child",
                    )
                ),
                _ti(
                    "# --- Remove Edge: Parent''O -> Child''O ---",
                    "IF( ElementIsComponent('Dim', 'Hier', 'Child''O', 'Parent''O') = 1 );",
                    "    HierarchyElementComponentDelete('Dim', 'Hier', 'Parent''O', 'Child''O');",
                    "ENDIF;",
                ),
                id="edge-delete",
            ),
            pytest.param(
                lambda: subset.build_subset_create_ti(
                    Subset(
                        name="Sub'O",
                        expression="{[Dim].[Hier].[E's]}",
                        source_path="/dimensions/Dim.hierarchies/Hier.subsets/Sub.json",
                    )
                ),
                _ti(
                    "# --- Create Subset: Sub''O in Hier ---",
                    "IF( HierarchySubsetExists('Dim', 'Hier', 'Sub''O') = 0 );",
                    "    HierarchySubsetCreate('Dim', 'Hier', 'Sub''O', 0);",
                    "ENDIF;",
                    "HierarchySubsetMDXSet('Dim', 'Hier', 'Sub''O', '{[Dim].[Hier].[E''s]}');",
                ),
                id="subset-create",
            ),
            pytest.param(
                lambda: subset.build_subset_update_ti(
                    Subset(
                        name="Sub'O",
                        expression="{[Dim].[Hier].[E's]}",
                        source_path="/dimensions/Dim.hierarchies/Hier.subsets/Sub.json",
                    )
                ),
                _ti(
                    "# --- Update Subset: Sub''O in Dim ---",
                    "IF( HierarchySubsetExists('Dim', 'Hier', 'Sub''O') = 1 );",
                    "    HierarchySubsetMDXSet('Dim', 'Hier', 'Sub''O', '{[Dim].[Hier].[E''s]}');",
                    "ENDIF;",
                ),
                id="subset-update",
            ),
            pytest.param(
                lambda: subset.build_subset_delete_ti(
                    Subset(
                        name="Sub'O",
                        expression="{[Dim].[Hier].[E's]}",
                        source_path="/dimensions/Dim.hierarchies/Hier.subsets/Sub.json",
                    )
                ),
                _ti(
                    "# --- Delete Subset: Sub''O from Dim ---",
                    "IF( HierarchySubsetExists('Dim', 'Hier', 'Sub''O') = 1 );",
                    "    HierarchySubsetDestroy('Dim', 'Hier', 'Sub''O');",
                    "ENDIF;",
                ),
                id="subset-delete",
            ),
            pytest.param(
                lambda: cube.build_cube_create_ti(
                    Cube(
                        name="Cube'O",
                        dimensions=[
                            Dimension(name="D1", hierarchies=[], defaultHierarchy=None, source_path="/dimensions/D1.json"),
                            Dimension(name="D'2", hierarchies=[], defaultHierarchy=None, source_path="/dimensions/D2.json"),
                        ],
                        rules=[],
                        views=[],
                        source_path="/cubes/Cube.json",
                    )
                ),
                _ti(
                    "# --- Create Cube: Cube''O ---",
                    "IF( CubeExists('Cube''O') = 0 );",
                    "    CubeCreate('Cube''O', 'D1', 'D''2');",
                    "ENDIF;",
                ),
                id="cube-create",
            ),
            pytest.param(
                lambda c=_rule_cube("['A']=N:2;"): cube.build_cube_update_ti(c),
                _ti(
                    "# --- Update Cube Rules: RuleCube ---",
                    "IF( CubeExists('RuleCube') = 1 );",
                    "    CubeRuleSet('RuleCube', '[''A'']=N:2;');",
                    "ENDIF;",
                ),
                id="cube-update",
            ),
            pytest.param(
                lambda: cube.build_cube_delete_ti(
                    Cube(
                        name="Cube'O",
                        dimensions=[],
                        rules=[],
                        views=[],
                        source_path="/cubes/Cube.json",
                    )
                ),
                _ti(
                    "# --- Delete Cube: Cube''O ---",
                    "IF( CubeExists('Cube''O') = 1 );",
                    "    CubeDestroy('Cube''O');",
                    "ENDIF;",
                ),
                id="cube-delete",
            ),
            pytest.param(
                lambda: mdxview.build_mdxview_create_ti(
                    MDXView(
                        name="View'O",
                        mdx="SELECT {[Dim].[Hier].[E's]} ON 0 FROM [Cube]",
                        source_path="/cubes/Cube.views/View.json",
                    )
                ),
                _ti(
                    "# --- Create MDX View: View''O in Cube: Cube ---",
                    "IF( ViewExists('Cube', 'View''O') = 0 );",
                    "    ViewCreateByMDX('Cube', 'View''O', 'SELECT {[Dim].[Hier].[E''s]} ON 0 FROM [Cube]', 0);",
                    "ENDIF;",
                ),
                id="mdxview-create",
            ),
            pytest.param(
                lambda: mdxview.build_mdxview_update_ti(
                    MDXView(
                        name="View'O",
                        mdx="SELECT {[Dim].[Hier].[E's]} ON 0 FROM [Cube]",
                        source_path="/cubes/Cube.views/View.json",
                    )
                ),
                _ti(
                    "# --- Update MDX View: View''O in Cube: Cube ---",
                    "IF( ViewExists('Cube', 'View''O') = 1 );",
                    "    ViewDestroy('Cube', 'View''O');",
                    "ENDIF;",
                    "ViewCreateByMDX('Cube', 'View''O', 'SELECT {[Dim].[Hier].[E''s]} ON 0 FROM [Cube]', 0);",
                ),
                id="mdxview-update",
            ),
            pytest.param(
                lambda: mdxview.build_mdxview_delete_ti(
                    MDXView(
                        name="View'O",
                        mdx="SELECT {[Dim].[Hier].[E's]} ON 0 FROM [Cube]",
                        source_path="/cubes/Cube.views/View.json",
                    )
                ),
                _ti(
                    "# --- Delete MDX View: View''O in Cube: Cube ---",
                    "IF( ViewExists('Cube', 'View''O') = 1 );",
                    "    ViewDestroy('Cube', 'View''O');",
                    "ENDIF;",
                ),
                id="mdxview-delete",
            ),
            pytest.param(
                lambda: nativeview.build_native_view_create_ti(
                    NativeView(
                        name="Native'O",
                        columns=[],
                        rows=[],
                        titles=[],
                        suppress_empty_columns=True,
                        suppress_empty_rows=True,
                        format_string="0.#########",
                        source_path="/cubes/Cube.views/Native.json",
                    )
                ),
                _ti(
                    "# --- Create Native View: Native''O in Cube: Cube ---",
                    "IF( ViewExists('Cube', 'Native''O') = 0 );",
                    "    ViewCreate('Cube', 'Native''O', 0);",
                    "ENDIF;",
                ),
                id="nativeview-create",
            ),
            pytest.param(
                lambda: nativeview.build_native_view_update_ti(
                    NativeView(
                        name="Native'O",
                        columns=[],
                        rows=[],
                        titles=[],
                        suppress_empty_columns=True,
                        suppress_empty_rows=True,
                        format_string="0.#########",
                        source_path="/cubes/Cube.views/Native.json",
                    )
                ),
                _ti(
                    "# --- Delete Native View: Native''O from Cube: Cube ---\r\n"
                    "IF( ViewExists('Cube', 'Native''O') = 1 );\r\n"
                    "    ViewDestroy('Cube', 'Native''O');\r\n"
                    "ENDIF;",
                    "# --- Create Native View: Native''O in Cube: Cube ---\r\n"
                    "IF( ViewExists('Cube', 'Native''O') = 0 );\r\n"
                    "    ViewCreate('Cube', 'Native''O', 0);\r\n"
                    "ENDIF;",
                ),
                id="nativeview-update",
            ),
            pytest.param(
                lambda: nativeview.build_native_view_delete_ti(
                    NativeView(
                        name="Native'O",
                        columns=[],
                        rows=[],
                        titles=[],
                        suppress_empty_columns=True,
                        suppress_empty_rows=True,
                        format_string="0.#########",
                        source_path="/cubes/Cube.views/Native.json",
                    )
                ),
                _ti(
                    "# --- Delete Native View: Native''O from Cube: Cube ---",
                    "IF( ViewExists('Cube', 'Native''O') = 1 );",
                    "    ViewDestroy('Cube', 'Native''O');",
                    "ENDIF;",
                ),
                id="nativeview-delete",
            ),
            pytest.param(
                lambda c=_rule_cube("['A']=N:1;"): cube.build_cube_update_ti(c),
                _ti(
                    "# --- Update Cube Rules: RuleCube ---",
                    "IF( CubeExists('RuleCube') = 1 );",
                    "    CubeRuleSet('RuleCube', '[''A'']=N:1;');",
                    "ENDIF;",
                ),
                id="rule-create-via-cube-update",
            ),
            pytest.param(
                lambda c=_rule_cube("['A']=N:2;"): cube.build_cube_update_ti(c),
                _ti(
                    "# --- Update Cube Rules: RuleCube ---",
                    "IF( CubeExists('RuleCube') = 1 );",
                    "    CubeRuleSet('RuleCube', '[''A'']=N:2;');",
                    "ENDIF;",
                ),
                id="rule-update-via-cube-update",
            ),
            pytest.param(
                lambda c=_rule_cube(""): cube.build_cube_update_ti(c),
                _ti(
                    "# --- Update Cube Rules: RuleCube ---",
                    "IF( CubeExists('RuleCube') = 1 );",
                    "    CubeRuleSet('RuleCube', '');",
                    "ENDIF;",
                ),
                id="rule-delete-via-cube-update",
            ),
        ],
    )
    def test_builders_generate_exact_ti(self, actual, expected):
        assert actual() == expected
