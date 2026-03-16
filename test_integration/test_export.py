import filecmp
from pathlib import Path
import pytest


from test_integration.test_base import load_fixture_model_tm1gitpy, export_check_no_errors, tm1_service
from TM1py import TM1Service
from tm1_git_py.exporter import export
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
        fixture_tm1gitpy_dir, _ = load_fixture_model_tm1gitpy(self)

        test_tm1gitpy_model = export_check_no_errors(self)

        with tempfile.TemporaryDirectory() as temp_dir:
        
            # when
            test_tm1gitpy_dir = str(Path(temp_dir) / "test_tm1gitpy_dir")
            serialize_model(test_tm1gitpy_model, test_tm1gitpy_dir)
            
            # then 
            cmp = filecmp.dircmp(test_tm1gitpy_dir, fixture_tm1gitpy_dir)
            assert not cmp.left_only, f"Files only in left directory: {cmp.left_only}"
            assert not cmp.right_only, f"Files only in right directory: {cmp.right_only}"
            assert not cmp.diff_files, f"Files that differ: {cmp.diff_files}"

    def test_export_filters_control_objects_with_skip_flags(self):
        model, errors = export(
            self.tm1_service,
            filter_rules=["-/cubes/}*"],
        )
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"

        assert all(not d.name.startswith("}") for d in model.dimensions)
        assert all(not c.name.startswith("}") for c in model.cubes)
        assert all(not p.name.startswith("}") for p in model.processes)

    def test_export_applies_custom_filter_rules_during_assembly(self):
        model, errors = export(
            self.tm1_service,
            filter_rules=[
                "-/dimensions/testdim1*",
                "-/cubes/testcube1*",
            ],
        )
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"

        dimension_paths = [d.source_path.lower() for d in model.dimensions]
        cube_paths = [c.source_path.lower() for c in model.cubes]
        assert not any(path.startswith("dimensions/testdim1") for path in dimension_paths)
        assert not any(path.startswith("cubes/testcube1") for path in cube_paths)

    def test_export_applies_tm1project_filter_style_rules(self):
        example_filter_path = Path(__file__).resolve().parents[1] / "examples" / "tm1project_filter.txt"
        example_rules = [
            line.strip()
            for line in example_filter_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        # Keep some real example lines and add fixture-targeted rules in the same format.
        filter_rules = [
            example_rules[0],  # +/data_csv/*.*
            example_rules[2],  # -/Cubes/Views*
            "-/Cubes/TestCube1*",
            "-/Dimensions/TestDim1*",
        ]

        model, errors = export(
            self.tm1_service,
            filter_rules=filter_rules,
        )
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"

        dimension_paths = [d.source_path.lower() for d in model.dimensions]
        cube_paths = [c.source_path.lower() for c in model.cubes]
        assert not any(path.startswith("dimensions/testdim1") for path in dimension_paths)
        assert not any(path.startswith("cubes/testcube1") for path in cube_paths)
