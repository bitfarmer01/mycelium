"""Timeout tiers (feature 26): follow-up < reflect by default, both env-configurable, both fire on a hung mock."""
from __future__ import annotations

import json
import subprocess
import time

import pytest

from core import llm

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "daemon_enabled", lambda: False)
    monkeypatch.setattr(llm, "USAGE_LOG", tmp_path / "usage.jsonl")
    for k in ("MYCELIUM_TIMEOUT_FOLLOW_UP_S", "MYCELIUM_TIMEOUT_REFLECT_S"):
        monkeypatch.delenv(k, raising=False)


def test_default_tiers_ordered():
    assert llm.BUCKET_TIMEOUTS["follow_up"] < llm.BUCKET_TIMEOUTS["reflect"]
    assert llm.bucket_timeout("follow_up") < llm.bucket_timeout("reflect")


def test_env_overrides_tiers(monkeypatch):
    monkeypatch.setenv("MYCELIUM_TIMEOUT_FOLLOW_UP_S", "5.5")
    monkeypatch.setenv("MYCELIUM_TIMEOUT_REFLECT_S", "90")
    assert llm.bucket_timeout("follow_up") == 5.5
    assert llm.bucket_timeout("reflect") == 90.0


def test_unknown_bucket_falls_back_to_60():
    assert llm.bucket_timeout("no_such_bucket") == 60.0


def test_explicit_timeout_s_wins_over_tier(monkeypatch):
    seen: dict = {}

    def fake_run(args, **kw):
        seen["timeout"] = kw.get("timeout")
        return subprocess.CompletedProcess(args=["claude"], returncode=0,
                                           stdout=json.dumps({"result": '{"x": 1}'}), stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm.call_json("sys", "user", SCHEMA, timeout_s=9.0, bucket="follow_up")
    assert seen["timeout"] == 9.0


def test_tier_reaches_subprocess_by_default(monkeypatch):
    seen: dict = {}

    def fake_run(args, **kw):
        seen["timeout"] = kw.get("timeout")
        return subprocess.CompletedProcess(args=["claude"], returncode=0,
                                           stdout=json.dumps({"result": '{"x": 1}'}), stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm.call_json("sys", "user", SCHEMA, bucket="follow_up")
    assert seen["timeout"] == llm.bucket_timeout("follow_up")


def test_both_tiers_fire_on_hung_mock(monkeypatch):
    """A hung CLI dies at the tier timeout: follow-up fires early, reflect only at its own (longer) budget."""
    env = {"MYCELIUM_TIMEOUT_FOLLOW_UP_S": "0.2", "MYCELIUM_TIMEOUT_REFLECT_S": "0.5"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    def hung_run(args, **kw):
        time.sleep(float(kw["timeout"]))  # ignores the deadline: hung, like a wedged CLI
        raise subprocess.TimeoutExpired(args, kw["timeout"])

    monkeypatch.setattr(llm.subprocess, "run", hung_run)

    t0 = time.monotonic()
    with pytest.raises(llm.LLMError, match="timed out"):
        llm.call_json("sys", "user", SCHEMA, bucket="follow_up")
    follow_up_elapsed = time.monotonic() - t0

    t0 = time.monotonic()
    with pytest.raises(llm.LLMError, match="timed out"):
        llm.call_json("sys", "user", SCHEMA, bucket="reflect")
    reflect_elapsed = time.monotonic() - t0

    assert follow_up_elapsed < 0.4
    assert reflect_elapsed >= 0.5
    assert follow_up_elapsed < reflect_elapsed
