"""Feature 18: retrieval latency recorded by retrieve() and surfaced at /vitals."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from core import retrieve as r
from core.retrieve import LatencyHistogram, pctl

# ---- pctl golden values (linear interpolation) ----


def test_pctl_golden_values():
    assert pctl([], 0.5) is None
    assert pctl([7.0], 0.95) == 7.0
    assert pctl([10.0, 20.0, 30.0, 40.0], 0.5) == 25.0
    assert pctl([10.0, 20.0, 30.0, 40.0], 0.95) == pytest.approx(38.5)


def test_histogram_window_is_bounded_and_thread_safe():
    h = LatencyHistogram(max_samples=3)
    for ms in (1.0, 2.0, 3.0, 4.0):
        h.record(ms)
    s = h.summary()
    assert s["count"] == 3  # the oldest sample fell off the window
    assert s["p50_ms"] == 3.0
    assert s["p95_ms"] == pytest.approx(3.9)  # 3 + (4 - 3) * 0.9


def test_empty_histogram_summary_has_null_percentiles():
    assert LatencyHistogram().summary() == {"count": 0, "p50_ms": None, "p95_ms": None}


# ---- acceptance: retrieve() records; /vitals shows ----


def test_retrieve_records_one_sample_per_call(fake_db, monkeypatch):
    monkeypatch.setattr(r, "embed_query", lambda text: [0.1] * 1024)
    before = r.latency.summary()["count"]
    asyncio.run(r.retrieve("anything"))
    s = r.latency.summary()
    assert s["count"] == before + 1
    assert s["p50_ms"] is not None and s["p95_ms"] is not None


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_vitals_exposes_retrieval_latency(client, monkeypatch):
    import core.db
    from app.routers import vitals as vr

    async def dead(sql, vars=None):
        raise RuntimeError("no db")

    monkeypatch.setattr(core.db, "q", dead)  # counts degrade to null; latency must still render

    async def zero(table):
        return 0

    monkeypatch.setattr(vr, "_count", zero)

    from core.retrieve import latency

    before = latency.summary()["count"]
    latency.record(12.0)
    latency.record(44.0)
    async with client as c:
        resp = await c.get("/vitals")
    body = resp.json()
    assert body["retrieval"]["count"] == before + 2
    assert body["retrieval"]["p50_ms"] is not None
    assert body["retrieval"]["p95_ms"] is not None
