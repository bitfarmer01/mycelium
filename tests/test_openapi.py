"""OpenAPI cleanup (feature 10): /docs renders with titles, tags, examples; no schema 500s."""
from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_docs_and_schema_render(client):
    async with client as c:
        docs = await c.get("/docs")
        schema_r = await c.get("/openapi.json")
    assert docs.status_code == 200 and "html" in docs.headers["content-type"]
    assert schema_r.status_code == 200
    schema = schema_r.json()
    assert schema["info"]["title"] == "Mycelium"
    assert schema["info"]["version"]
    assert "learns and proves it" in schema["info"]["description"]


async def test_every_route_has_tags(client):
    async with client as c:
        schema = (await c.get("/openapi.json")).json()
    assert schema["paths"], "no routes in schema"
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            if method in ("parameters", "servers"):  # path-level keys, not operations
                continue
            assert op.get("tags"), f"{method.upper()} {path} has no tags"


async def test_ask_request_body_has_example(client):
    async with client as c:
        schema = (await c.get("/openapi.json")).json()
    body = schema["paths"]["/ask"]["post"]["requestBody"]["content"]["application/json"]
    assert body["example"] == {"text": "who owns billing?", "session_id": "sess-123"}
    assert "typical" in body["examples"]


async def test_schema_generation_is_stable_and_never_500s(client):
    # Feature 10's "no 500s on schema generation": generate repeatedly, in and
    # out of a request cycle, and require identical output.
    from app.server import app

    first = app.openapi()
    async with client as c:
        second = (await c.get("/openapi.json")).json()
    assert first == second
