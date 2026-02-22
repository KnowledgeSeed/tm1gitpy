import os
import socket
from pathlib import Path
from typing import Optional

import pytest
import requests
from testcontainers.compose import DockerCompose

from tm1_git_py.config import TM1ServerConfig, TM1ServersConfig
from tm1_git_py.main import _tm1_connection_from_config

import logging

logger = logging.getLogger(__name__)

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


def resolve_tm1models_dir(request: pytest.FixtureRequest) -> str:
    """Resolve the path to the tm1models directory based on the test file location."""
    test_file_dir = Path(request.fspath.dirname)
    tm1models_path = test_file_dir / "tm1models"
    
    if tm1models_path.exists():
        return str(tm1models_path)
    
    raise ValueError(f"tm1models directory not found at expected location: {tm1models_path}")


@pytest.fixture(scope="class")
def tm1_environment(request: pytest.FixtureRequest):
    """
    Fixture to start the TM1 environment using docker-compose.
    It waits for the TM1 API to be available before yielding the connection.
    """
    test_dir = request.config.rootdir / "test_integration"
    force_container_start = os.getenv("FORCE_CONTAINER_START", "False").lower() in ("true", "1", "t")
    
    compose: Optional[DockerCompose] = None
    
    port=5360
    if force_container_start or not is_tm1_running(port):
        os.environ["TM1MODELS_DIR"] = resolve_tm1models_dir(request)
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