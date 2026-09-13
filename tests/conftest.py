import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client
