import filecmp
from pathlib import Path
import pytest


from test_integration.test_base import load_fixture_model, export_check_no_errors, tm1_service
from TM1py import TM1Service
from tm1_git_py.serializer import serialize_model

import tempfile
import filecmp

@pytest.mark.usefixtures("tm1_service")
class TestExport:

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service : TM1Service = tm1_service

    def test_export_no_error_matching_folder(self):
        
        # given
        fixture_dir, _ = load_fixture_model(self)

        model = export_check_no_errors(self)

        with tempfile.TemporaryDirectory() as temp_dir:
        
            # when
            export_dir = str(Path(temp_dir) / "exported_model")
            serialize_model(model, export_dir)
            
            # then 
            cmp = filecmp.dircmp(export_dir, fixture_dir)
            assert not cmp.left_only, f"Files only in left directory: {cmp.left_only}"
            assert not cmp.right_only, f"Files only in right directory: {cmp.right_only}"
            assert not cmp.diff_files, f"Files that differ: {cmp.diff_files}"