import socket

import pytest
from alembic import command
from alembic.config import Config

from campushallen.database.session import make_engine


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """All tests must use fakes or HTTPX's MockTransport, never real API traffic."""

    def forbidden(*args, **kwargs):
        raise AssertionError("Real network access is forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def database(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    engine = make_engine(url)
    yield engine
    engine.dispose()
