import filecmp
import tempfile
from pathlib import Path

import pytest
import TM1py
from TM1py import Cube, Dimension, Hierarchy, TM1Service

from test_integration.test_base import export_check_no_errors, load_fixture_model_tm1gitpy, tm1_service
from tm1_git_py.changeset import ChangeType, Changeset
from tm1_git_py.comparator import Comparator
from tm1_git_py.serializer import serialize_model

@pytest.mark.usefixtures("tm1_service")
class TestChangesetApply:

    _f_no_meta_obj = [ "-/cubes/}*", "-/dimensions/}*"]

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service : TM1Service = tm1_service

    @staticmethod
    def _changes_by(changeset: Changeset, change_type: ChangeType, class_name: str):
        return [
            change.body for change in changeset.changes
            if change.change_type == change_type and change.body.__class__.__name__ == class_name
        ]

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

    # -----------------------------------------------------------------------
    # TI Process tests
    # -----------------------------------------------------------------------

    _f_no_meta = ["-/cubes/}*", "-/dimensions/}*", "-/processes/}*"]

    
    def test_create_process_no_meta_objects(self):
        """Changeset should re-create a process that was deleted from the server."""
        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta)

        self.tm1_service.processes.delete("myprocess2")
        model = export_check_no_errors(self, self._f_no_meta)

        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta)

        # then
        added_processes = self._changes_by(changeset, ChangeType.ADD, "Process")
        assert len(added_processes) == 1
        assert added_processes[0].name == "myprocess2"
        self.check_no_diff(fixture_dir, model)

    def test_delete_process_no_meta_objects(self):
        """Changeset should remove a process that does not exist in the fixture."""
        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta)

        extra_process = TM1py.Process(name="TestExtraProcess", datasource_type="None")
        self.tm1_service.processes.create(extra_process)
        model = export_check_no_errors(self, self._f_no_meta)

        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta)

        # then
        removed_processes = self._changes_by(changeset, ChangeType.REMOVE, "Process")
        assert len(removed_processes) == 1
        assert removed_processes[0].name == "TestExtraProcess"
        self.check_no_diff(fixture_dir, model)

    def test_create_process_add_only_no_meta_objects(self):
        """In add_only mode, missing processes should be created."""
        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta)

        self.tm1_service.processes.delete("myprocess2")
        model = export_check_no_errors(self, self._f_no_meta)

        # when
        changeset = self.compare(model, fixture_model, mode='add_only')
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta)

        # then
        added_processes = self._changes_by(changeset, ChangeType.ADD, "Process")
        assert len(added_processes) >= 1
        assert any(o.name == "myprocess2" for o in added_processes)
        self.check_no_diff(fixture_dir, model)

    def test_delete_process_add_only_no_meta_objects(self):
        """In add_only mode, extra processes should NOT be removed."""
        # given
        fixture_dir, fixture_model = load_fixture_model_tm1gitpy(self, self._f_no_meta)

        extra_process = TM1py.Process(name="TestExtraProcess2", datasource_type="None")
        self.tm1_service.processes.create(extra_process)
        model = export_check_no_errors(self, self._f_no_meta)

        # when
        changeset = self.compare(model, fixture_model, mode='add_only')
        self.apply(changeset)

        # then — nothing should have been removed
        assert not self._changes_by(changeset, ChangeType.REMOVE, "Process")

        # cleanup
        self.tm1_service.processes.delete("TestExtraProcess2")

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

    def compare(self, source, target, mode :str = 'full'):
        comparator = Comparator()
        return comparator.compare(source, target, mode=mode)
        
    
    def apply(self, changeset: Changeset):
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
            cmp = filecmp.dircmp(export_dir, expected_dir)
            
            # then 
            assert not cmp.left_only, f"Files only in left directory: {cmp.left_only}"
            assert not cmp.right_only, f"Files only in right directory: {cmp.right_only}"
            assert not cmp.diff_files, f"Files that differ: {cmp.diff_files}"
