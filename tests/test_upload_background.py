"""Background upload ingestion (feature 7): /upload answers 202 immediately with
a job id, the heavy chunk/embed/store work runs in the background, and the
follow-up status field flips to done (or failed) when ingest finishes.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import httpx
import pytest

WAIT_S = 5.0


def _tmp_dir() -> Path:
    return Path(tempfile.mkdtemp())


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _fresh_jobs():
    from app.routers import retrieval

    retrieval._jobs.clear()
    yield
    retrieval._jobs.clear()


async def _wait_done(client, job_id: str) -> dict:
    deadline = time.monotonic() + WAIT_S
    body: dict = {}
    while time.monotonic() < deadline:
        r = await client.get(f"/upload/status/{job_id}")
        body = r.json()
        if body["status"] in ("done", "failed"):
            return body
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished: {body}")


async def test_upload_returns_202_then_status_flips_to_done(monkeypatch, client):
    import scripts.ingest as ing
    from app.routers import retrieval

    stored = {}
    tmp = _tmp_dir()
    monkeypatch.setattr(retrieval, "UPLOAD_DIR", tmp)
    monkeypatch.setattr(ing, "embed", lambda texts: [[0.0] * 1024 for _ in texts])

    async def fake_store(chunks, sources, replace_paths=None):
        stored["chunks"], stored["sources"] = chunks, sources

    monkeypatch.setattr(ing, "store", fake_store)
    files = [
        ("files", ("Team Handbook.md", b"# Handbook\n\nThe on-call owner is the platform team.", "text/markdown")),
        ("files", ("plans.csv", b"plan,price\nStart,0\n", "text/csv")),
        ("files", ("evil.exe", b"MZ", "application/octet-stream")),
    ]
    async with client as c:
        r = await c.post("/upload", files=files)
        assert r.status_code == 202, "upload must answer immediately, before embedding"
        body = r.json()
        assert body["status"] in ("pending", "running")
        assert body["job_id"]
        assert {f["name"] for f in body["files"]} == {"Team_Handbook.md", "plans.csv"}
        assert [x["name"] for x in body["rejected"]] == ["evil.exe"]
        assert (tmp / "Team_Handbook.md").exists() and not (tmp / "evil.exe").exists()

        job = await _wait_done(c, body["job_id"])
    assert job["status"] == "done"
    result = job["result"]
    assert {f["name"] for f in result["files"]} == {"Team_Handbook.md", "plans.csv"}
    assert result["chunks"] == 2 and len(stored["sources"]) == 2
    assert job["error"] is None


async def test_failed_ingest_reports_failed_with_reason(monkeypatch, client):
    import scripts.ingest as ing
    from app.routers import retrieval

    tmp = _tmp_dir()
    monkeypatch.setattr(retrieval, "UPLOAD_DIR", tmp)
    monkeypatch.setattr(ing, "embed", lambda texts: [[0.0] * 1024 for _ in texts])

    async def broken_store(chunks, sources, replace_paths=None):
        raise ConnectionError("db down")

    monkeypatch.setattr(ing, "store", broken_store)
    async with client as c:
        r = await c.post("/upload", files=[("files", ("a.md", b"# A\n\nhello", "text/markdown"))])
        assert r.status_code == 202
        job = await _wait_done(c, r.json()["job_id"])
    assert job["status"] == "failed"
    assert "database unavailable" in job["error"]
    assert "ConnectionError" in job["error"]


async def test_too_many_chunks_fails_the_job_and_removes_files(monkeypatch, client):
    import scripts.ingest as ing
    from app.routers import retrieval

    tmp = _tmp_dir()
    monkeypatch.setattr(retrieval, "UPLOAD_DIR", tmp)
    monkeypatch.setattr(retrieval, "UPLOAD_MAX_CHUNKS", 1)
    called = []
    monkeypatch.setattr(ing, "embed", lambda texts: called.append(1) or [[0.0] * 1024 for _ in texts])
    big = ("files", ("big.txt", (" ".join(["word"] * 900)).encode(), "text/plain"))
    async with client as c:
        r = await c.post("/upload", files=[big])
        assert r.status_code == 202 and not called  # rejected before embedding
        job = await _wait_done(c, r.json()["job_id"])
    assert job["status"] == "failed"
    assert "exceeds the limit" in job["error"]
    assert not (tmp / "big.txt").exists()


async def test_busy_ingest_still_gets_429(monkeypatch, client):
    import scripts.ingest as ing
    from app.routers import retrieval

    monkeypatch.setattr(retrieval, "UPLOAD_DIR", _tmp_dir())
    monkeypatch.setattr(ing, "embed", lambda texts: [[0.0] * 1024 for _ in texts])
    async with client as c:
        await retrieval._upload_lock.acquire()
        try:
            r = await c.post("/upload", files=[("files", ("a.md", b"# A\n\nhi", "text/markdown"))])
            assert r.status_code == 429
        finally:
            retrieval._upload_lock.release()


async def test_unknown_job_404(client):
    async with client as c:
        r = await c.get("/upload/status/deadbeef")
    assert r.status_code == 404


def test_evict_jobs_keeps_table_bounded():
    from app.routers import retrieval

    jobs = {f"{i:012x}": {"job_id": f"{i:012x}"} for i in range(retrieval._JOBS_MAX + 10)}
    retrieval.evict_jobs(jobs, retrieval._JOBS_MAX)
    assert len(jobs) == retrieval._JOBS_MAX
    assert "000000000000" not in jobs, "oldest job evicted first"
    assert f"{retrieval._JOBS_MAX + 9:012x}" in jobs, "newest job survives"
