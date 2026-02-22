import os
import filecmp
import sys
from pathlib import Path
import pytest

from tm1_git_py.changeset import Changeset

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
TEST_INTEGRATION_DIR = PROJECT_ROOT / "test_integration"
sys.path.insert(0, str(TEST_INTEGRATION_DIR))

from test_base import tm1_environment
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

@pytest.mark.usefixtures("tm1_environment")
class TestExportIntegration:

    _f_no_meta_obj = ["-/cubes/}*", "-/dimensions/}*"]

    @pytest.fixture(autouse=True)
    def _tm1(self, tm1_environment):
        self.tm1_service : TM1Service = tm1_environment

    def test_create_cube_no_meta_objects(self):
        
        # given
        expected_dir, test_model = self.load_test_model(self._f_no_meta_obj)

        self.tm1_service.cubes.delete("mycube")
        model = self.export_check_no_errors(self._f_no_meta_obj)
        
        # when
        changeset = self.compare(model, test_model)
        self.apply(changeset)
        model = self.export_check_no_errors(self._f_no_meta_obj)

        # then
        self.check_no_diff(expected_dir, model)

    @pytest.mark.skip(reason="Ignoring test_create_cube_with_meta_objects")
    def test_create_cube_with_meta_objects(self):
        
        # given
        expected_dir, test_model = self.load_test_model([])

        self.tm1_service.cubes.delete("mycube")
        model = self.export_check_no_errors([])
        
        # when
        changeset = self.compare(model, test_model)
        self.apply(changeset)
        model = self.export_check_no_errors([])

        # then
        self.check_no_diff(expected_dir, model)

    def test_delete_cube_no_meta_objects(self):
        
        # given
        expected_dir, test_model = self.load_test_model(self._f_no_meta_obj)
        
        self.tm1_service.cubes.create(Cube("TestCube", dimensions=["mydimension", "mydimension2"]))
        model = self.export_check_no_errors(self._f_no_meta_obj)
        
        # when
        changeset = self.compare(model, test_model)
        self.apply(changeset)
        model = self.export_check_no_errors(self._f_no_meta_obj)

        # then
        self.check_no_diff(expected_dir, model)

    def test_delete_cube_with_meta_objects(self):
        
        # given
        expected_dir, test_model = self.load_test_model([])
        
        self.tm1_service.cubes.create(Cube("TestCube", dimensions=["mydimension", "mydimension2"]))
        model = self.export_check_no_errors([])
        
        # when
        changeset = self.compare(model, test_model)
        self.apply(changeset)
        model = self.export_check_no_errors([])

        # then
        self.check_no_diff(expected_dir, model)

    def test_delete_cube_add_only(self):
        
        # given
        expected_dir, test_model = self.load_test_model()
        
        self.tm1_service.cubes.create(Cube("TestCube", dimensions=["mydimension", "mydimension2"]))
        model = self.export_check_no_errors()
        
        # when
        changeset = self.compare(model, test_model, mode='add_only')
        self.apply(changeset)
        assert not changeset.removed

        self.check_no_diff(expected_dir, model)

    def test_create_dimension(self):
        
        # given
        expected_dir, test_model = self.load_test_model()

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        model = self.export_check_no_errors()
        
        # when
        changeset = self.compare(model, test_model)
        self.apply(changeset)

        self.check_no_diff(expected_dir, model)

    def test_delete_dimension(self):
        
        # given
        expected_dir, test_model = self.load_test_model()

        dimension = Dimension("TestDimension")
        dimension.add_hierarchy(Hierarchy(dimension_name="TestDimension", name= "TestDimension"))
        self.tm1_service.dimensions.create(dimension)
        model = self.export_check_no_errors()
        
        # when
        changeset = self.compare(model, test_model)
        self.apply(changeset)

        self.check_no_diff(expected_dir, model)

    def compare(self, source, target, mode :str = 'full'):
        comparator = Comparator()
        return comparator.compare(source, target, mode=mode)
        
    
    def apply(self, changeset: Changeset):
        status_dir = 'tests'
        exec_id = 'test_create_and_delete'
        success, _errors = changeset.apply(tm1_service=self.tm1_service, status_dir=status_dir, execution_id=exec_id, batch=False)
        assert success, f"Changeset application failed with errors: {_errors}"


    def export_check_no_errors(self, filter_rules: list[str] = None):
        model, errors = export(self.tm1_service)
        assert isinstance(model, Model)
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"
        if filter_rules is None:
            filter_rules = self._f_no_meta_obj
        return filter(model, filter_rules)
    
    def load_test_model(self, filter_rules: list[str] = None):
        expected_dir = str(Path(__file__).parent / "exported_model")
        test_model, errors = deserialize_model(expected_dir)
        if filter_rules is None:
            filter_rules = self._f_no_meta_obj
        return expected_dir, filter(test_model, filter_rules)


    def check_no_diff(self, expected_dir, model):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = str(Path(temp_dir) / "exported_model")
            serialize_model(model, export_dir)
            cmp = filecmp.dircmp(export_dir, expected_dir)
            
            # then 
            assert not cmp.left_only, f"Files only in left directory: {cmp.left_only}"
            assert not cmp.right_only, f"Files only in right directory: {cmp.right_only}"
            assert not cmp.diff_files, f"Files that differ: {cmp.diff_files}"
