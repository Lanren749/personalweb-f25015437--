import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["FSG_STATE_FILE"] = str(Path(tempfile.mkdtemp(prefix="fsg-mvp-")) / "state.json")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.main import app, store  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def reset_state():
    auth.clear_sessions()
    store.reset()
    yield


def login(client, username: str, password: str = "demo123") -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}
