import os
from unittest.mock import patch

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-docker", action="store_true", default=False, help="run docker integration tests"
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: mark test as requiring docker containers")
    config.addinivalue_line(
        "markers", "postgres: mark test as requiring a disposable PostgreSQL database"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-docker"):
        skip_docker = pytest.mark.skip(reason="need --run-docker option to run")
        for item in items:
            if "docker" in item.keywords:
                item.add_marker(skip_docker)


@pytest.fixture
def mock_env():
    """Fixture to ensure environment is clean while retaining Windows home discovery."""
    # Streamlit resolves its config directory with Path.home(). On Windows that
    # requires these profile variables, so clearing every variable makes its
    # AppTest runner fail before application code executes. They contain no
    # project configuration or secret and are retained only for OS path lookup.
    home_environment = {
        name: os.environ[name]
        for name in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH")
        if name in os.environ
    }
    with patch.dict(os.environ, home_environment, clear=True):
        yield
