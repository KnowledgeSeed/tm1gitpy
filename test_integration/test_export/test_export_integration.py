import os
import filecmp
import sys
from pathlib import Path
from typing import Dict
from unittest.mock import patch
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
TEST_INTEGRATION_DIR = PROJECT_ROOT / "test_integration"
sys.path.insert(0, str(TEST_INTEGRATION_DIR))

from test_base import tm1_environment
from TM1py import TM1Service
from tm1_git_py.exporter import export
from tm1_git_py.deserializer import deserialize_model
from tm1_git_py.serializer import serialize_model
from tm1_git_py.model.model import Model
from tm1_git_py.filter import filter

import tempfile
import shutil
import filecmp

@pytest.mark.usefixtures("tm1_environment")
class TestExportIntegration:

    tm1_environment : TM1Service = None

    @pytest.fixture(autouse=True)
    def _tm1(self, tm1_environment):
        self.tm1_service = tm1_environment

    def test_export_no_error_matching_folder(self):
        
        # given
        model, errors = export(self.tm1_service)
        assert isinstance(model, Model)
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"

        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = str(Path(temp_dir) / "exported_model")
            
            # when
            serialize_model(model, export_dir)
            
            expected_dir = str(Path(__file__).parent / "exported_model")
            cmp = filecmp.dircmp(export_dir, expected_dir)
            
        # then 
        assert not cmp.left_only, f"Files only in left directory: {cmp.left_only}"
        assert not cmp.right_only, f"Files only in right directory: {cmp.right_only}"
        assert not cmp.diff_files, f"Files that differ: {cmp.diff_files}"
