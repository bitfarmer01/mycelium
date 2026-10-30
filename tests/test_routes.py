"""Route-equivalence suite: pins every route's path, handler binding, methods, and status codes.

Written against the pre-split monolith (app/server.py at commit 94f6a6c). The
per-segment router split must keep this suite green — it is the zero-behavior-
change gate the router workstreams build on.

Route presence and handler bindings are asserted through the public routing API
(`app.url_path_for`) and OpenAPI, not by walking `app.routes`: newer FastAPI
(0.143+) registers included routers as composites there, and the effective
table is what behavior depends on.
"""
from __future__ import annotations

import sys
import types

import httpx
import pytest

# (path, methods sans HEAD, handler name, websocket) — the monolith's routes plus
# additive post-split routes (ops probes, workstream A).
ROUTE_MANIFEST = [
    ("/", {"GET"}, "index", False),
    ("/ask", {"POST"}, "ask", False),
    ("/ask/stream", {"POST"}, "ask_stream", False),
    ("/ask/stream/demo", {"GET"}, "ask_stream_demo", False),
    ("/config", {"GET"}, "config", False),
    ("/confirm", {"POST"}, "confirm", False),
    ("/correct", {"POST"}, "correct", False),
    ("/events", set(), "events", True),
    ("/exam/burst", {"POST"}, "exam_burst", False),
    ("/exam/history", {"GET"}, "exam_history", False),
    ("/healthz", {"GET"}, "healthz", False),
    ("/improve", {"POST"}, "improve", False),
    ("/readyz", {"GET"}, "readyz", False),
    ("/reflect", {"POST"}, "reflect", False),
    ("/stt", {"POST"}, "stt", False),
    ("/tick", {"POST"}, "tick", False),
    ("/tts", {"GET"}, "tts", False),
    ("/tts/stream", {"GET"}, "tts_stream", False),
    ("/upload", {"POST"}, "upload", False),
    ("/vitals", {"GET"}, "vitals", False),
]

OPENAPI_METHODS = {path: sorted(m.lower() for m in methods) for path, methods, _name, websocket in ROUTE_MANIFEST if not websocket}


def test_every_route_keeps_path_and_handler():
    from app.server import app

    for path, _methods, name, _websocket in ROUTE_MANIFEST:
        assert app.url_path_for(name) == path, f"route for handler {name!r} moved or vanished"


def test_static_mount_survives():
    from app.server import app

    mounts = [r for r in app.routes if r.__class__.__name__ == "Mount"]
    assert any(r.path == "/static" and r.name == "static" for r in mounts)


def _stub(monkeypatch, name: str, **funcs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in funcs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


@pytest.fixture
def probed_client(monkeypatch):
    """Client over the real app with every core module stubbed and no DB/CLI/network."""
    import core.db

    async def fake_q(sql, vars=None):
        if "config:main" in sql:
            return [{"k": "v"}]
        raise RuntimeError("no db")

    monkeypatch.setattr(core.db, "q", fake_q)

    async def ask(text, session_id):
        return {"spoken": "ok", "question_id": "question:x"}

    async def correct(question_id, to_role, confidence=1.0, actor=""):
        return {"ok": True}

    async def confirm(question_id):
        return {"ok": True}

    async def decay_tick():
        return 1

    async def reflect():
        return {"spoken": "s", "sentences": [{"text": "s", "row_ids": ["ledger:1"]}]}

    async def improve():
        return {"candidates": []}

    def stt(data, content_type):
        return "hello"

    class SttUploadError(ValueError):  # matches core.voice's interface
        status_code = 415

    def validate_stt_upload(data, content_type=None, filename=None):
        return content_type or "audio/webm"

    def tts(text, quality=None):
        return b"audio"

    def tts_stream(text, quality=None):
        return iter([b"audio"])

    _stub(monkeypatch, "core.reflex", ask=ask)
    _stub(monkeypatch, "core.learn", correct=correct, confirm=confirm, decay_tick=decay_tick)
    _stub(monkeypatch, "core.reflect", reflect=reflect)
    _stub(monkeypatch, "core.growth", improve=improve, slot_version=lambda: "v1", load_slot=lambda: "s", slot_hash=lambda t: "h")
    _stub(monkeypatch, "core.voice", stt=stt, SttUploadError=SttUploadError, validate_stt_upload=validate_stt_upload, tts=tts, tts_stream=tts_stream)

    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


PROBES = [
    ("GET /", "get", "/", None, 200),
    ("POST /ask", "post", "/ask", {"json": {"text": "q", "session_id": "s"}}, 200),
    ("POST /ask missing field", "post", "/ask", {"json": {}}, 422),
    ("POST /correct", "post", "/correct", {"json": {"question_id": "question:x", "to_role": "intent"}}, 200),
    ("POST /correct bad role", "post", "/correct", {"json": {"question_id": "question:x", "to_role": "nope"}}, 422),
    ("POST /confirm", "post", "/confirm", {"json": {"question_id": "question:x"}}, 200),
    ("POST /tick", "post", "/tick", {"json": {}}, 200),
    ("POST /reflect", "post", "/reflect", {"json": {}}, 200),
    ("POST /improve", "post", "/improve", {"json": {}}, 200),
    ("POST /stt", "post", "/stt", {"files": {"audio": ("a.webm", b"x", "audio/webm")}}, 200),
    ("GET /tts", "get", "/tts", {"params": {"text": "hi"}}, 200),
    ("GET /tts quality selector", "get", "/tts", {"params": {"text": "hi", "quality": "v3"}}, 200),
    ("GET /tts missing param", "get", "/tts", None, 422),
    ("GET /tts/stream", "get", "/tts/stream", {"params": {"text": "hi"}}, 200),
    ("GET /tts/stream missing param", "get", "/tts/stream", None, 422),
    ("GET /vitals", "get", "/vitals", None, 200),
    ("GET /config", "get", "/config", None, 200),
    ("POST /upload no files", "post", "/upload", None, 422),
    ("GET /docs", "get", "/docs", None, 200),
    ("GET /openapi.json", "get", "/openapi.json", None, 200),
    ("GET /static/index.html", "get", "/static/index.html", None, 200),
    ("GET /events over http", "get", "/events", None, 404),
    ("GET unknown path", "get", "/nope", None, 404),
]


@pytest.mark.parametrize(("label", "method", "path", "kwargs", "expected"), PROBES, ids=[p[0] for p in PROBES])
async def test_route_status_codes(label, method, path, kwargs, expected, probed_client):
    async with probed_client as c:
        r = await getattr(c, method)(path, **(kwargs or {}))
    assert r.status_code == expected, f"{label}: {r.status_code} != {expected}"


async def test_docs_and_openapi_still_render_every_route(probed_client):
    async with probed_client as c:
        docs = await c.get("/docs")
        schema = await c.get("/openapi.json")
    assert docs.status_code == 200 and "html" in docs.headers["content-type"]
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    # The monolith's route table must never shrink; additive workstream routes
    # (B /search, D /learning/*, …) extend it on top.
    assert set(OPENAPI_METHODS) <= set(paths), "openapi lost monolith routes"
    for path, methods in OPENAPI_METHODS.items():
        assert methods <= sorted(paths[path]), f"{path}: openapi methods shrank"
