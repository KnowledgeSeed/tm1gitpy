import filecmp
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Optional

import pytest
import requests
from testcontainers.compose import DockerCompose

from tm1_git_py.config import TM1ServerConfig, TM1ServersConfig
from tm1_git_py.deserializer import deserialize_model
from tm1_git_py.exporter import export
from tm1_git_py.main import _tm1_connection_from_config
from tm1_git_py.model.model import Model
from tm1_git_py.filter import filter

logger = logging.getLogger(__name__)

# Directories under a serialized model root to compare in integration "no diff" checks.
# Other paths (e.g. ``.internal`` internal artifacts) are ignored.
MODEL_COMPARE_SUBDIRS = ("dimensions", "cubes", "chores", "processes")


def _assert_dircmp_trees_equal(left: str, right: str) -> None:
    """Recursively assert two directories have identical file sets and contents."""
    cmp = filecmp.dircmp(left, right)
    assert not cmp.left_only, (
        f"Files/dirs only in exported model under {left!r}: {sorted(cmp.left_only)}"
    )
    assert not cmp.right_only, (
        f"Files/dirs only in expected under {right!r}: {sorted(cmp.right_only)}"
    )
    assert not cmp.diff_files, (
        f"Files that differ under {left!r} vs {right!r}: {sorted(cmp.diff_files)}"
    )
    for name in sorted(cmp.common_dirs):
        _assert_dircmp_trees_equal(
            os.path.join(left, name),
            os.path.join(right, name),
        )


def assert_export_matches_expected_subdirs(actual_root: str, expected_root: str) -> None:
    """
    Compare only model payload directories between two serialized model roots.

    Ignores siblings such as ``.internal`` or any other top-level entries not listed
    in ``MODEL_COMPARE_SUBDIRS``.
    """
    for sub in MODEL_COMPARE_SUBDIRS:
        left_p = Path(actual_root) / sub
        right_p = Path(expected_root) / sub
        left_ex = left_p.is_dir()
        right_ex = right_p.is_dir()
        if not left_ex and not right_ex:
            continue
        assert left_ex and right_ex, (
            f"Subdirectory {sub!r} must exist on both sides when comparing "
            f"(left exists={left_ex}, right exists={right_ex}, "
            f"actual_root={actual_root!r}, expected_root={expected_root!r})"
        )
        _assert_dircmp_trees_equal(str(left_p), str(right_p))


@pytest.fixture(scope="class")
def tm1_service(request: pytest.FixtureRequest):
    """
    Fixture to start the TM1 environment using docker-compose.
    It waits for the TM1 API to be available before yielding the connection.
    """
    test_dir = request.config.rootdir / "test_integration"
    force_container_start = os.getenv("FORCE_CONTAINER_START", "False").lower() in ("true", "1", "t")
    
    compose: Optional[DockerCompose] = None
    
    port=5360
    if force_container_start or not is_tm1_running(port):
        os.environ["TM1MODELS_DIR"] = resolve_test_model_dir(request)
        port = find_free_port()
        os.environ["TM1_PORT"] = str(port)
        
        compose = DockerCompose(
            context=str(test_dir),
            compose_file_name="docker-compose.yml",
            pull=False,
        )

        logger.info("Starting TM1 Docker containers...")
        compose.start()
        compose.wait_for(f"http://localhost:{port}/api/v1/")
    else:
        logger.info("TM1 is already running or docker start is disabled.")
        
    # Yield the TM1Service connection to the tests
    yield _tm1_connection_from_config(get_test_config(port), "local")

    # Teardown
    if compose:
        print("Stopping TM1 Docker containers...")
        compose.stop()
        
        # Clean up the environment variables
        if "TM1MODELS_DIR" in os.environ:
            del os.environ["TM1MODELS_DIR"]
        if "TM1_PORT" in os.environ:
            del os.environ["TM1_PORT"]

def is_tm1_running(port: int, timeout_in_seconds: int = 1) -> bool:
    try:
        response = requests.get(f"http://localhost:{port}/api/v1/", timeout=timeout_in_seconds)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def find_free_port() -> int:
    """Find an available port on the host machine."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def get_test_config(port: int) -> TM1ServersConfig:
    """Create TM1ServersConfig for testing."""
    config = TM1ServersConfig()
    config.servers = {
        "local": TM1ServerConfig(
            name="local",
            base_url=f"http://localhost:{port}/api/v1",
            user="admin",
            password=""
        )
    }
    return config


def resolve_test_model_dir(request: pytest.FixtureRequest) -> str:
    """Resolve the path to the test_model directory based on the test file location."""
    test_file_dir = Path(request.fspath.dirname)
    test_model_path = test_file_dir / "fixture_model_tm1bin"
    
    if test_model_path.exists():
        return str(test_model_path)
    
    raise ValueError(f"test_model directory not found at expected location: {test_model_path}")


def load_fixture_model_tm1gitpy(obj, filter_rules: list[str] = None) -> tuple[str, Model]:
    dir_path = get_dir(obj)
    fixture_dir = str(Path(dir_path) / "fixture_model_tm1gitpy")
    fixture_model, errors = deserialize_model(fixture_dir)
    return fixture_dir, filter(fixture_model, filter_rules) if filter_rules else fixture_model


def load_fixture_changeset(obj, filter_rules: list[str] = None) -> tuple[str, Model]:
    dir_path = get_dir(obj)
    fixture_dir = str(Path(dir_path) / "fixture_changeset")
    fixture_model, errors = deserialize_model(fixture_dir)
    return fixture_dir, filter(fixture_model, filter_rules) if filter_rules else fixture_model


def get_dir(obj) -> str:
    module = sys.modules[obj.__class__.__module__]
    file_path = os.path.abspath(module.__file__)
    dir_path = os.path.dirname(file_path)
    return dir_path


def export_check_no_errors(
    self,
    filter_rules: list[str] = None,
    *,
    internal_model_dir: Optional[str] = None,
) -> Model:
    model, errors = export(
        self.tm1_service,
        filter_rules_list=filter_rules,
        internal_model_dir=internal_model_dir,
    )
    assert isinstance(model, Model)
    for category, category_errors in errors.items():
        assert not category_errors, f"Found errors in {category}: {category_errors}"
    return filter(model, filter_rules) if filter_rules else model


def check_no_diff(expected: Model, model: Model):
    def _sorted_by_name(items):
        return sorted(items, key=lambda item: getattr(item, "name", ""))

    assert _sorted_by_name(model.processes) == _sorted_by_name(expected.processes)
    assert _sorted_by_name(model.chores) == _sorted_by_name(expected.chores)

    for dim, expected_dim in zip(_sorted_by_name(model.dimensions), _sorted_by_name(expected.dimensions)):
        assert dim.name == expected_dim.name
        for hier, expected_hier in zip(_sorted_by_name(dim.hierarchies), _sorted_by_name(expected_dim.hierarchies)):
            assert hier.name == expected_hier.name
            assert _sorted_by_name(hier.elements) == _sorted_by_name(expected_hier.elements)
            assert _sorted_by_name(hier.edges) == _sorted_by_name(expected_hier.edges)
            assert _sorted_by_name(hier.subsets) == _sorted_by_name(expected_hier.subsets)

    for cube, expected_cube in zip(_sorted_by_name(model.cubes), _sorted_by_name(expected.cubes)):
        assert cube.name == expected_cube.name
        assert _sorted_by_name(cube.views) == _sorted_by_name(expected_cube.views)
