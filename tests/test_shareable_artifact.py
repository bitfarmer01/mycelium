"""Feature 49: a public read-only link renders the last burst with no auth token and no possible ledger mutation."""
from __future__ import annotations

import httpx
import pytest

CAND_ROWS = [{"name": "cite-every-claim", "axis": "constraints", "status": "scored", "veto_reason": None,
              "verdict": "PASS", "mean": 0.82, "spread": 0.1, "delta": 0.3, "ratio": 1.1,
              "runner": "daytona", "ts": "2026-10-30T00:00:00Z"}]
POISON_ROWS = [{"name": "poison-no-cite", "status": "detected", "veto_reason": None, "mean": 0.1, "verdict": "VETO"}]


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_share_renders_last_burst_without_auth(client, fake_db):

    fake_db.rows["SELECT * FROM candidate"] = CAND_ROWS
    fake_db.rows["SELECT name, status, veto_reason"] = POISON_ROWS
    async with client as c:
        r = await c.get("/exam/share")  # deliberately no Authorization header
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "cite-every-claim" in body and "PASS" in body and "poison-no-cite" in body
    assert "<script" not in body and "<form" not in body, "static markup only — nothing to POST or run"


async def test_share_never_mutates_the_ledger(client, fake_db):
    fake_db.rows["SELECT * FROM candidate"] = CAND_ROWS
    fake_db.rows["SELECT name, status, veto_reason"] = POISON_ROWS
    async with client as c:
        await c.get("/exam/share")
    assert all(sql.startswith("SELECT") for sql, _ in fake_db.calls), \
        f"read-only means read-only: {fake_db.sql()}"


async def test_share_escapes_candidate_fields(client, fake_db):

    evil = [dict(CAND_ROWS[0], name="<script>alert(1)</script>")]
    fake_db.rows["SELECT * FROM candidate"] = evil
    fake_db.rows["SELECT name, status, veto_reason"] = []
    async with client as c:
        body = (await c.get("/exam/share")).text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


async def test_share_empty_state(client, fake_db):
    async with client as c:
        r = await c.get("/exam/share")
    assert r.status_code == 200
    assert "No exam results yet" in r.text


async def test_share_degrades_when_db_is_down(client, fake_db):
    def dead(sql, vars=None):
        raise ConnectionError("refused")

    fake_db.rows["FROM candidate"] = dead
    async with client as c:
        r = await c.get("/exam/share")
    assert r.status_code == 200, "a public link renders degraded, never a 500"
    assert "unavailable" in r.text


def test_revert_is_a_known_ledger_type():
    from core.contracts import LEDGER_TYPES

    assert "revert" in LEDGER_TYPES
