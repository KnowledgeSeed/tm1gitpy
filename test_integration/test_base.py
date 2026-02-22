import os
from pathlib import Path
from typing import Optional

import pytest
import requests
from testcontainers.compose import DockerCompose

from tm1_git_py.config import TM1ServerConfig, TM1ServersConfig
from tm1_git_py.main import _tm1_connection_from_config

import logging

logger = logging.getLogger(__name__)

def is_tm1_running(timeout_in_seconds: int = 1) -> bool:
    """Check if the TM1 API is currently accessible."""
    try:
        response = requests.get("http://localhost:5360/api/v1/", timeout=timeout_in_seconds)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def get_test_config() -> TM1ServersConfig:
    """Create TM1ServersConfig for testing."""
    config = TM1ServersConfig()
    config.servers = {
        "local": TM1ServerConfig(
            name="local",
            base_url="http://localhost:5360/api/v1",
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
    allow_docker_start = os.getenv("ALLOW_DOCKER_START", "True").lower() in ("true", "1", "t")
    
    compose: Optional[DockerCompose] = None
    
    if not is_tm1_running() and allow_docker_start:

        os.environ["TM1MODELS_DIR"] = resolve_tm1models_dir(request)
        
        # Initialize Docker Compose
        compose = DockerCompose(
            context=str(test_dir),
            compose_file_name="docker-compose.yml",
            pull=False,  # Set to False to avoid arm64 manifest errors on Mac
        )

        logger.info("Starting TM1 Docker containers...")
        compose.start()
        compose.wait_for("http://localhost:5360/api/v1/")
    else:
        logger.info("TM1 is already running or docker start is disabled.")
        
    # Yield the TM1Service connection to the tests
    yield _tm1_connection_from_config(get_test_config(), "local")

    # Teardown
    if compose:
        print("Stopping TM1 Docker containers...")
        compose.stop()
        
        # Clean up the environment variable
        if "TM1MODELS_DIR" in os.environ:
            del os.environ["TM1MODELS_DIR"]