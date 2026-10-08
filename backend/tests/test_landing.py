"""MVP-UI-003 (landing-page): the backend root answers humans.

A healthy server used to answer 404 on ``/`` -- the coordinator opened the port
from a browser and the console looked broken. The root now serves a static
entry page (links to health/docs/spec and the frontend), the favicon is a
quiet 204, and the loopback Host guard covers both.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from tests.claim_support import make_database

LOOPBACK = "http://127.0.0.1"


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(make_database(tmp_path)), base_url=LOOPBACK)


def test_root_serves_the_landing_page(tmp_path):
    response = _client(tmp_path).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    for door in ("/health", "/docs", "/spec/status", "5173"):
        assert door in response.text, f"the landing page must point at {door}"


def test_favicon_is_a_quiet_204(tmp_path):
    response = _client(tmp_path).get("/favicon.ico")

    assert response.status_code == 204
    assert not response.content


def test_the_root_routes_stay_out_of_the_openapi_schema(tmp_path):
    schema = _client(tmp_path).get("/openapi.json").json()

    assert "/" not in schema["paths"]
    assert "/favicon.ico" not in schema["paths"]
    assert "/api/v1/projects" in schema["paths"], "the real surface is untouched"


def test_the_host_guard_covers_the_root_path(tmp_path):
    response = _client(tmp_path).get("/", headers={"host": "evil.example"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "auth.host_header_rejected"
