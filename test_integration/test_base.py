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


def export_check_no_errors(self, filter_rules: list[str] = None)  -> Model:
    model, errors = export(self.tm1_service)
    assert isinstance(model, Model)
    for category, category_errors in errors.items():
        assert not category_errors, f"Found errors in {category}: {category_errors}"
    return filter(model, filter_rules) if filter_rules else model

