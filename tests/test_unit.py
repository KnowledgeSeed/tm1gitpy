from pathlib import Path
from typing import TypeVar

import pytest

import tm1_git_py.comparator
from tm1_git_py import serialize_model
from tm1_git_py.changeset import Changeset
from tm1_git_py.deserializer import *
from tm1_git_py.model import Cube, Dimension, Process, Chore, Hierarchy, MDXView, Subset, Element, TI, Model
from config import (
    _build_mock_changeset_data,
    _objects_equal_case_builders,
    build_mock_model,
    dim_data,
    chore_data,
    process_data
)

T = TypeVar('T', Cube, Dimension, Process, Chore)


TEST_ROOT = Path(__file__).resolve().parent
test_model_dir_base = TEST_ROOT / "model_test_export" / "test_model_base"
test_model_dir_broken = TEST_ROOT / "model_test_export" / "test_model_broken"
test_model_dir_diff = TEST_ROOT / "model_test_export" / "test_model_diff"


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
            elements=[Element({"Name": "Item 1", "Type": "Numeric"})],
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
        
        comparator = tm1_git_py.Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        assert len(changeset.changes) == 0


    def test_comparator_has_changes_add_only(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = tm1_git_py.Comparator()
        changeset = comparator.compare(model1, model2, mode='add_only')

        assert len(changeset.added) == 2
        assert len(changeset.modified) == 3
        assert len(changeset.removed) == 0


    def test_comparator_has_changes_full(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = tm1_git_py.Comparator()
        changeset = comparator.compare(model1, model2, mode='full')

        assert len(changeset.added) == 2
        assert len(changeset.modified) == 3
        assert len(changeset.removed) == 2


    def test_comparator_dimensions_change_propagation(self):
        """Test if adding a new Subset does not propagate as a change to the Dimension object"""
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = tm1_git_py.Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        changes = str(changeset)
        added = [added for added in changeset.added if type(added) is Subset]
        modified = [modified['new'] for modified in changeset.modified if type(modified['new']) is Hierarchy]

        assert changes.count('/dimensions') == 2
        assert (isinstance(added[0], Subset) and added[0].name == "}Temp_Subset_Discount")
        assert (isinstance(modified[0], Hierarchy) and modified[0].name == "testbenchMeasureSales")


    def test_comparator_cubes_change_propagation(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = tm1_git_py.Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        changes = str(changeset)
        added = [added for added in changeset.added if type(added) is MDXView]
        removed = [removed for removed in changeset.removed if type(removed) is MDXView]
        modified = [modified for modified in changeset.modified if type(modified['new']) is Cube]

        assert changes.count('/cubes') == 3

        assert (isinstance(added[0], MDXView) and added[0].name == "tm1_bedrock_py_gp0vkg064lilmmga")
        assert (isinstance(modified[0]['new'], Cube) and modified[0]['new'].name == "testbenchSales")
        assert (modified[0]['old'].rules != modified[0]['new'].rules)
        assert (isinstance(removed[0], MDXView) and removed[0].name == "tm1_bedrock_py_fp0vkg064lilmmga")


    def test_comparator_process_change_propagation(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = tm1_git_py.Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        changes = str(changeset)
        removed = [removed for removed in changeset.removed if type(removed) is Process]

        assert changes.count('/processes') == 1
        assert (isinstance(removed[0], Process) and removed[0].name == "Mock Process Load Product Data")


    def test_comparator_chores_change_propagation(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = tm1_git_py.Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        changes = str(changeset)
        modified = [modified['new'] for modified in changeset.modified if type(modified['new']) is Chore]

        assert changes.count('/chores') == 1
        assert (isinstance(modified[0], Chore) and modified[0].name == "Mock Nightly Maintenance")


    def test_sort_changes(self):
        changeset = Changeset()
        fixture = self.mock_changeset_data
        changeset.added = [
            fixture['process_added'],
            fixture['dimension_added'],
            fixture['subset_added']
        ]
        changeset.removed = [fixture['cube_removed']]
        changeset.modified = [{
            'old': fixture['hierarchy_old'],
            'new': fixture['hierarchy_new'],
            'changes': "Hierarchy updated"
        }]

        changes = changeset._ensure_changes()

        expected_order = [
            "C  /dimensions/MockDim",
            "C  /dimensions/MockDim.hierarchies/MockHier.subsets/NewSubset",
            "C  /processes/MockProcess",
            "U  /dimensions/MockDim.hierarchies/MockHier",
            "D  /cubes/MockCube"
        ]

        assert changes == expected_order
        assert [obj.name for obj in changeset.added] == [
            "MockDim",
            "NewSubset",
            "MockProcess"
        ]


@pytest.fixture(params=list(_objects_equal_case_builders().keys()), ids=list(_objects_equal_case_builders().keys()))
def objects_equal_data(request):
    builders = _objects_equal_case_builders()
    return builders[request.param]()
