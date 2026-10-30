"""Vitals panel (feature 64): /vitals metrics schema contract, percentile math,
middleware sampling, and the /events vitals feed wiring.

Backend checks run against the ASGI app; console checks are static-file
contracts following tests/test_console_modules.py — no browser needed here.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from app import metrics
from app.server import app

STATIC_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"

METRICS_KEYS = {"latency_ms_p50", "latency_ms_p95", "error_rate", "sample_count"}


@pytest.fixture(autouse=True)
def _clean_metrics():
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# --- percentile / error-rate math (pure) --------------------------------------


def test_percentile_nearest_rank() -> None:
    values = sorted([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])
    assert metrics.percentile(values, 0.5) == 50.0
    assert metrics.percentile(values, 0.95) == 100.0
    assert metrics.percentile([42.0], 0.95) == 42.0


def test_percentile_empty_is_none() -> None:
    assert metrics.percentile([], 0.5) is None


def test_error_rate_counts_only_server_errors() -> None:
    assert metrics.error_rate([]) is None
    assert metrics.error_rate([200, 200, 503, 404]) == 0.25
    assert metrics.error_rate([500, 500]) == 1.0


def test_sample_window_is_bounded() -> None:
    for i in range(metrics.MAX_SAMPLES + 50):
        metrics.record(200, float(i))
    snap = metrics.snapshot()
    assert snap["sample_count"] == metrics.MAX_SAMPLES


# --- /vitals schema contract ----------------------------------------------------


async def test_vitals_schema_includes_metrics_block(client, fake_db, monkeypatch, tmp_path) -> None:
    from app.routers import vitals

    metrics.record(200, 25.0)  # a prior request; the /vitals call itself can't sample itself
    monkeypatch.setattr(vitals, "BASELINE_PATH", tmp_path / "missing.json")
    async with client as c:
        r = await c.get("/vitals")
    assert r.status_code == 200
    body = r.json()
    assert set(body["metrics"]) == METRICS_KEYS
    assert body["metrics"]["latency_ms_p50"] == 25.0
    assert body["metrics"]["sample_count"] >= 1
    assert 0.0 <= body["metrics"]["error_rate"] <= 1.0


def test_vitals_feed_msg_shape() -> None:
    metrics.record(200, 12.5)
    metrics.record(500, 40.0)
    msg = metrics.vitals_feed_msg()
    assert msg["table"] == "vitals"
    assert msg["action"] == "METRICS"
    assert set(msg["row"]["metrics"]) == METRICS_KEYS
    assert msg["row"]["metrics"]["error_rate"] == 0.5


# --- middleware sampling --------------------------------------------------------


async def test_middleware_records_request_sample(client) -> None:
    async with client as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    snap = metrics.snapshot()
    assert snap["sample_count"] >= 1
    assert snap["latency_ms_p50"] is not None
    assert snap["error_rate"] == 0.0


# --- console wiring (static contracts, feature 64) -------------------------------


def test_events_dispatcher_handles_vitals_table() -> None:
    src = (STATIC_JS / "events.js").read_text(encoding="utf-8")
    assert re.search(r'msg\.table\s*===\s*"vitals"', src), (
        "events.js must route the /events vitals message type to the panel"
    )
    assert "updateMetricsRow" in src


def test_vitals_panel_renders_p50_p95_errors_and_corpus() -> None:
    src = (STATIC_JS / "vitals.js").read_text(encoding="utf-8")
    for label in ("p50", "p95", "errors"):
        assert f'"{label}"' in src, f"vitals panel must render the {label} cell"
    for field in ("latency_ms_p50", "latency_ms_p95", "error_rate"):
        assert field in src, f"vitals panel must read metrics.{field}"
    assert "corpus" in src, "vitals panel must render corpus size"
    assert "updateMetricsRow" in src.split("export")[0]


def test_mock_feed_broadcasts_vitals_frames() -> None:
    src = (STATIC_JS / "ws.js").read_text(encoding="utf-8")
    assert 'table: "vitals"' in src, "mock WebSocket must replay the vitals metrics frames"
    assert (STATIC_JS / "fixtures.js").read_text(encoding="utf-8").find("metricsFrames") != -1
