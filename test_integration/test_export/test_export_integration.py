import os
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

# Add the tm1_git_py directory to sys.path so we can import main and its dependencies
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
# TM1_GIT_PY_DIR = PROJECT_ROOT / "tm1_git_py"
# sys.path.insert(0, str(TM1_GIT_PY_DIR))

# Add test_integration to sys.path to import test_base
TEST_INTEGRATION_DIR = PROJECT_ROOT / "test_integration"
sys.path.insert(0, str(TEST_INTEGRATION_DIR))

from test_base import tm1_environment
from TM1py import TM1Service

@pytest.mark.usefixtures("tm1_environment")
class TestExportIntegration:

    tm1_environment : TM1Service = None

    @pytest.fixture(autouse=True)
    def _tm1(self, tm1_environment):
        self.tm1_service = tm1_environment

    def test_export_cubes(self):
        
        print("Starting test_export_cubes")

    def test_export_dimensions(self):
        print("Starting test_export_dimensions")