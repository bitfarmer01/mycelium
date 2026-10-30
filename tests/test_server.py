"""FastAPI surface tests: stubbed core modules, no CLI/DB/network."""
from __future__ import annotations

import sys
import types

import httpx
import pytest


def _stub(monkeypatch, name: str, **funcs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in funcs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_ask_returns_payload_and_audio_url(monkeypatch, client):
    seen = {}

    async def ask(text, session_id):
        seen["args"] = (text, session_id)
        return {"question_id": "question:q1", "spoken": "Hi there & bye", "screen": "x"}

    _stub(monkeypatch, "core.reflex", ask=ask)
    async with client as c:
        r = await c.post("/ask", json={"text": "who owns billing?", "session_id": "s1"})
    assert r.status_code == 200
    body = r.json()
    assert body["question_id"] == "question:q1"
    assert body["audio_url"] == "/tts?text=Hi%20there%20%26%20bye"
    assert seen["args"] == ("who owns billing?", "s1")


async def test_ask_defaults_session_id(monkeypatch, client):
    seen = {}

    async def ask(text, session_id):
        seen["sid"] = session_id
        return {"spoken": "ok"}

    _stub(monkeypatch, "core.reflex", ask=ask)
    async with client as c:
        r = await c.post("/ask", json={"text": "q"})
    assert r.status_code == 200
    assert seen["sid"]


async def test_ask_works_without_voice_module(monkeypatch, client):
    async def ask(text, session_id):
        return {"spoken": "typed answer"}

    _stub(monkeypatch, "core.reflex", ask=ask)
    monkeypatch.setitem(sys.modules, "core.voice", None)  # import core.voice -> ImportError
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    async with client as c:
        r = await c.post("/ask", json={"text": "q", "session_id": "s"})
        t = await c.get("/tts", params={"text": "hello"})
    assert r.status_code == 200
    assert r.json()["spoken"] == "typed answer"
    assert t.status_code in (502, 503)


async def test_ask_missing_reflex_is_503(monkeypatch, client):
    monkeypatch.setitem(sys.modules, "core.reflex", None)
    async with client as c:
        r = await c.post("/ask", json={"text": "q"})
    assert r.status_code == 503


async def test_correct_roundtrip(monkeypatch, client):
    async def correct(question_id, to_role):
        return {"question_id": question_id, "to_role": to_role, "ledger": 2}

    _stub(monkeypatch, "core.learn", correct=correct)
    async with client as c:
        r = await c.post("/correct", json={"question_id": "question:q1", "to_role": "intent"})
        bad = await c.post("/correct", json={"question_id": "question:q1", "to_role": "nonsense"})
    assert r.status_code == 200
    assert r.json() == {"question_id": "question:q1", "to_role": "intent", "ledger": 2}
    assert bad.status_code == 422


async def test_reflect_route(monkeypatch, client):
    async def reflect():
        return {
            "sentences": [{"text": "Intent wins on policy.", "row_ids": ["ledger:1"]}],
            "rows_read": 5,
            "dropped": 0,
            "llm": True,
            "spoken": "Intent wins on policy.",
        }

    _stub(monkeypatch, "core.reflect", reflect=reflect)
    async with client as c:
        r = await c.post("/reflect", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["sentences"][0]["row_ids"] == ["ledger:1"]
    assert body["audio_url"].startswith("/tts?text=Intent%20wins")


async def test_vitals_without_baseline_is_null(monkeypatch, client, tmp_path):
    import app.server as server
    import core.db

    async def boom(sql, vars=None):
        raise RuntimeError("no db")

    monkeypatch.setattr(core.db, "q", boom)
    monkeypatch.setattr(server, "BASELINE_PATH", tmp_path / "missing.json")
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    _stub(monkeypatch, "core.growth", slot_version=lambda: "v1.0", load_slot=lambda: "slot", slot_hash=lambda t: "abc")
    async with client as c:
        r = await c.get("/vitals")
    assert r.status_code == 200
    body = r.json()
    assert body["baseline"] is None
    assert body["runner"] == "local"
    assert body["slot_version"] == "v1.0"
    assert body["slot_hash"] == "abc"
    assert set(body["counts"]) == {"chunk", "question", "lesson", "candidate"}
    assert all(v is None for v in body["counts"].values())


async def test_root_serves_placeholder_or_index(client):
    async with client as c:
        r = await c.get("/")
    assert r.status_code == 200
    assert "html" in r.headers["content-type"]


async def test_upload_ingests_supported_and_rejects_others(monkeypatch, tmp_path):
    import httpx

    import app.routers.retrieval as retrieval
    import app.server as srv
    import scripts.ingest as ing

    stored = {}
    monkeypatch.setattr(retrieval, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(ing, "embed", lambda texts: [[0.0] * 1024 for _ in texts])

    async def fake_store(chunks, sources, replace_paths=None):
        stored["chunks"], stored["sources"] = chunks, sources

    monkeypatch.setattr(ing, "store", fake_store)
    files = [
        ("files", ("Team Handbook.md", b"# Handbook\n\nThe on-call owner is the platform team.", "text/markdown")),
        ("files", ("plans.csv", b"plan,price\nStart,0\n", "text/csv")),
        ("files", ("evil.exe", b"MZ", "application/octet-stream")),
    ]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test") as c:
        r = await c.post("/upload", files=files)
    assert r.status_code == 200
    body = r.json()
    assert {f["name"] for f in body["files"]} == {"Team_Handbook.md", "plans.csv"}
    assert body["chunks"] == 2 and len(stored["sources"]) == 2
    assert [x["name"] for x in body["rejected"]] == ["evil.exe"]
    assert (tmp_path / "Team_Handbook.md").exists() and not (tmp_path / "evil.exe").exists()


async def test_upload_limits_chunk_cap_and_busy(monkeypatch, tmp_path):
    import httpx

    import app.routers.retrieval as retrieval
    import app.server as srv
    import scripts.ingest as ing

    monkeypatch.setattr(retrieval, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(retrieval, "UPLOAD_MAX_CHUNKS", 1)
    called = []
    monkeypatch.setattr(ing, "embed", lambda texts: called.append(1) or [[0.0] * 1024 for _ in texts])
    big = ("files", ("big.txt", (" ".join(["word"] * 900)).encode(), "text/plain"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test") as c:
        r = await c.post("/upload", files=[big])
        assert r.status_code == 413 and "exceeds the limit" in r.json()["detail"]
        assert not called and not (tmp_path / "big.txt").exists()  # rejected before embedding, file removed
        await retrieval._upload_lock.acquire()
        try:
            r = await c.post("/upload", files=[("files", ("a.md", b"# A\n\nhi", "text/markdown"))])
            assert r.status_code == 429
        finally:
            retrieval._upload_lock.release()
