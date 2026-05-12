import filecmp
import tempfile
from pathlib import Path

import pytest
from TM1py import TM1Service

from test_integration.test_base import (
    DEFAULT_MAX_WORKERS,
    load_fixture_model_tm1gitpy,
    export_check_no_errors,
    tm1_service,
)
from tm1_git_py.services.exporter import export
from tm1_git_py.services.serializer import serialize_model


@pytest.mark.usefixtures("tm1_service")
class TestExport:

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service : TM1Service = tm1_service

    def test_export_no_error_matching_folder(self):
        
        # given
        fixture_tm1gitpy_dir, _ = load_fixture_model_tm1gitpy(self)

        with tempfile.TemporaryDirectory() as temp_dir:
        
            # when
            test_tm1gitpy_dir = str(Path(temp_dir) / "test_tm1gitpy_dir")
            test_tm1gitpy_model = export_check_no_errors(self, model_id=Path(test_tm1gitpy_dir).name)
            serialize_model(
                test_tm1gitpy_model,
                test_tm1gitpy_dir,
                max_workers=DEFAULT_MAX_WORKERS,
            )
            
            # then 
            cmp = filecmp.dircmp(test_tm1gitpy_dir, fixture_tm1gitpy_dir)
            assert not cmp.left_only, f"Files only in left directory: {cmp.left_only}"
            assert not cmp.right_only, f"Files only in right directory: {cmp.right_only}"
            assert not cmp.diff_files, f"Files that differ: {cmp.diff_files}"

    def test_export_filters_control_objects_with_skip_flags(self):
        model, errors = export(
            self.tm1_service,
            model_id="integration-export",
            filter_rules_list=["Cubes('}*')", "Dimensions('}*')", "Processes('}*')"],
            max_workers=DEFAULT_MAX_WORKERS,
        )
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"

        assert all(not d.name.startswith("}") for d in model.dimensions)
        assert all(not c.name.startswith("}") for c in model.cubes)
        assert all(not p.name.startswith("}") for p in model.processes)

    def test_export_applies_custom_filter_rules_during_assembly(self):
        model, errors = export(
            self.tm1_service,
            model_id="integration-export",
            filter_rules_list=[
                "Dimensions('TestDim1*')",
                "Cubes('TestCube1*')",
                "Cubes('TestCube2*')",
                "Cubes('TestCube3*')",
            ],
            max_workers=DEFAULT_MAX_WORKERS,
        )
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"

        dimension_names = [d.name for d in model.dimensions]
        cube_names = [c.name for c in model.cubes]
        assert not any(name.startswith("TestDim1") for name in dimension_names)
        assert not any(name.startswith("TestCube1") for name in cube_names)
        assert not any(name.startswith("TestCube2") for name in cube_names)
        assert not any(name.startswith("TestCube3") for name in cube_names)

    def test_export_applies_tm1project_filter_style_rules(self):
        example_filter_path = Path(__file__).resolve().parents[1] / "examples" / "tm1project_filter.txt"
        filter_rules = [
            line.strip()
            for line in example_filter_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        model, errors = export(
            self.tm1_service,
            model_id="integration-export",
            filter_rules_list=filter_rules,
            max_workers=DEFAULT_MAX_WORKERS,
        )
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"

        dimension_names = [d.name for d in model.dimensions]
        cube_names = [c.name for c in model.cubes]
        assert not any(name.startswith("Channel") for name in dimension_names)
        assert not any(name.startswith("zSYS Maintenance Parameter") for name in cube_names)

    def test_export_force_includes_technical_cube_while_other_technical_objects_stay_ignored(self):
        forced_cube_name = "}StatsByCube"

        model, errors = export(
            self.tm1_service,
            model_id="integration-export-force-technical-cube",
            filter_rules_list=[f"!Cubes('{forced_cube_name}')"],
            max_workers=DEFAULT_MAX_WORKERS,
        )
        for category, category_errors in errors.items():
            assert not category_errors, f"Found errors in {category}: {category_errors}"

        cube_names = [c.name for c in model.cubes]
        assert forced_cube_name in cube_names
        assert "}StatsByRule" not in cube_names
        assert all(not d.name.startswith("}") for d in model.dimensions)
        assert all(not p.name.startswith("}") for p in model.processes)
