import os
import filecmp
import sys
from pathlib import Path
from typing import Dict
from unittest.mock import patch
import pytest

from tm1_git_py import changeset
from tm1_git_py.changeset import Changeset

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
TEST_INTEGRATION_DIR = PROJECT_ROOT / "test_integration"
sys.path.insert(0, str(TEST_INTEGRATION_DIR))

from test_base import tm1_environment
from TM1py import TM1Service, Cube
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

    @pytest.fixture(autouse=True)
    def _tm1(self, tm1_environment):
        self.tm1_service : TM1Service = tm1_environment

    def test_delete_cube(self):
        
        # given
        expected_dir, test_model = self.load_test_model()
        
        self.tm1_service.cubes.create(Cube("TestCube", dimensions=["mydimension", "mydimension2"]))
        model = self.export_without_errors()
        
        # when
        changeset = self.compare(test_model, model)
        self.apply(changeset)

        self.verify_no_diff(expected_dir, model)


    def test_create_cube(self):
        
        # given
        expected_dir, test_model = self.load_test_model()

        self.tm1_service.cubes.delete("mycube")
        model = self.export_without_errors()
        
        # when
        changeset = self.compare(test_model, model)
        self.apply(changeset)

        self.verify_no_diff(expected_dir, model)


    def compare(self, source, target, mode :str = 'full'):
        comparator = Comparator()
        return comparator.compare(source, target, mode=mode)
        
    
    def apply(self, changeset: Changeset):
        status_dir = 'tests'
        exec_id = 'test_create_and_delete'
        success, _errors = changeset.apply(tm1_service=self.tm1_service, status_dir=status_dir, execution_id=exec_id, batch=False)
        assert success, f"Changeset application failed with errors: {_errors}"


    def export_without_errors(self):
        model, errors = export(self.tm1_service)
        assert isinstance(model, Model)
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"
        return model
    
    def load_test_model(self):
        expected_dir = str(Path(__file__).parent / "exported_model")
        test_model, errors = deserialize_model(expected_dir)
        return expected_dir,test_model


    def verify_no_diff(self, expected_dir, model):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = str(Path(temp_dir) / "exported_model")
            serialize_model(model, export_dir)
            cmp = filecmp.dircmp(export_dir, expected_dir)
            
            # then 
            assert not cmp.left_only, f"Files only in left directory: {cmp.left_only}"
            assert not cmp.right_only, f"Files only in right directory: {cmp.right_only}"
            assert not cmp.diff_files, f"Files that differ: {cmp.diff_files}"
