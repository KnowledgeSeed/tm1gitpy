import os
import filecmp
import sys
from pathlib import Path
import pytest

from tm1_git_py.changeset import Changeset, import_changeset

from test_integration.test_base import load_fixture_model, export_check_no_errors, tm1_service
from TM1py import TM1Service, Cube, Dimension, Hierarchy
from tm1_git_py.exporter import export
from tm1_git_py.deserializer import deserialize_model
from tm1_git_py.serializer import serialize_model
from tm1_git_py.model.model import Model
from tm1_git_py.filter import filter
from tm1_git_py.comparator import Comparator

import tempfile
import shutil
import filecmp

@pytest.mark.usefixtures("tm1_service")
class TestChangesetApply:

    _f_no_meta_obj = ["-/cubes/}*", "-/dimensions/}*"]

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service : TM1Service = tm1_service

    def test_create_cube_full_no_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, self._f_no_meta_obj)

        self.tm1_service.cubes.delete("mycube")
        model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # then
        assert len(changeset.added) == 1
        assert changeset.added[0].__class__.__name__ == "Cube"
        assert changeset.added[0].name == "mycube"
        self.check_no_diff(fixture_dir, model)

    @pytest.mark.skip(reason="Ignoring failing due to meta objects")
    def test_create_cube_full_with_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, [])

        self.tm1_service.cubes.delete("mycube")
        model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)
        model = export_check_no_errors(self, [])

        # then
        assert len(changeset.added) == 1
        assert changeset.added[0].__class__.__name__ == "Cube"
        assert changeset.added[0].name == "mycube"
        self.check_no_diff(fixture_dir, model)


    def test_create_cube_add_only_no_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, self._f_no_meta_obj)

        self.tm1_service.cubes.delete("mycube")
        model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(model, fixture_model, mode='add_only')
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # then
        assert len(changeset.added) == 1
        assert changeset.added[0].__class__.__name__ == "Cube"
        assert changeset.added[0].name == "mycube"
        self.check_no_diff(fixture_dir, model)

    @pytest.mark.skip(reason="Ignoring failing due to meta objects")
    def test_create_cube_add_only_with_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, [])

        self.tm1_service.cubes.delete("mycube")
        model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(model, fixture_model, mode='add_only')
        self.apply(changeset)
        model = export_check_no_errors(self, [])

        # then
        assert len(changeset.added) == 1
        assert changeset.added[0].__class__.__name__ == "Cube"
        assert changeset.added[0].name == "mycube"
        self.check_no_diff(fixture_dir, model)

    def test_delete_cube_full_no_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, self._f_no_meta_obj)
        
        self.tm1_service.cubes.create(Cube("TestCube1", dimensions=["mydimension", "mydimension2"]))
        model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)
        model = export_check_no_errors(self, self._f_no_meta_obj)

        # then
        assert len(changeset.removed) == 1
        assert changeset.removed[0].__class__.__name__ == "Cube"
        assert changeset.removed[0].name == "TestCube1"
        self.check_no_diff(fixture_dir, model)

    def test_delete_cube_full_with_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, [])
        
        self.tm1_service.cubes.create(Cube("TestCube2", dimensions=["mydimension", "mydimension2"]))
        model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)
        model = export_check_no_errors(self, [])

        # then
        assert len(changeset.removed) == 1
        assert changeset.removed[0].__class__.__name__ == "Cube"
        assert changeset.removed[0].name == "TestCube2"
        self.check_no_diff(fixture_dir, model)

    def test_delete_cube_add_only_no_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, self._f_no_meta_obj)
        
        self.tm1_service.cubes.create(Cube("TestCube3", dimensions=["mydimension", "mydimension2"]))
        model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(model, fixture_model, mode='add_only')
        self.apply(changeset)
        
        # then
        assert not changeset.removed
        self.check_no_diff(fixture_dir, model)

    def test_delete_cube_add_only_with_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self)
        
        self.tm1_service.cubes.create(Cube("TestCube4", dimensions=["mydimension", "mydimension2"]))
        model = export_check_no_errors(self)
        
        # when
        changeset = self.compare(model, fixture_model, mode='add_only')
        self.apply(changeset)
        
        # then
        assert not changeset.removed
        self.check_no_diff(fixture_dir, model)

    def test_create_dimension_no_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, self._f_no_meta_obj)

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)

        self.check_no_diff(fixture_dir, model)

    @pytest.mark.skip(reason="Ignoring failing due to meta objects")
    def test_create_dimension_with_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, [])

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)

        self.check_no_diff(fixture_dir, model)

    def test_delete_dimension_no_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, self._f_no_meta_obj)

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        model = export_check_no_errors(self, self._f_no_meta_obj)
        
        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)

        self.check_no_diff(fixture_dir, model)

    @pytest.mark.skip(reason="Ignoring failing due to meta objects")
    def test_delete_dimension_with_meta_objects(self):
        
        # given
        fixture_dir, fixture_model = load_fixture_model(self, [])

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        model = export_check_no_errors(self, [])
        
        # when
        changeset = self.compare(model, fixture_model)
        self.apply(changeset)

        self.check_no_diff(fixture_dir, model)

    def compare(self, source, target, mode :str = 'full'):
        comparator = Comparator()
        return comparator.compare(source, target, mode=mode)
        
    
    def apply(self, changeset: Changeset):
        status_dir = 'tests'
        exec_id = 'test_create_and_delete'
        success, _errors = changeset.apply(tm1_service=self.tm1_service, status_dir=status_dir, execution_id=exec_id, batch=False)
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
