"""Session response cache (feature 25): an identical in-session question replays the cached answer, no model call."""
from __future__ import annotations

import json
import subprocess

import pytest

from core import llm

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def _proc():
    return subprocess.CompletedProcess(args=["claude"], returncode=0,
                                       stdout=json.dumps({"result": '{"x": 5}'}), stderr="")


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    llm.reset_session_cache()
    monkeypatch.setattr(llm, "daemon_enabled", lambda: False)
    monkeypatch.setattr(llm, "USAGE_LOG", tmp_path / "usage.jsonl")
    yield
    llm.reset_session_cache()


def test_second_identical_call_is_cached(monkeypatch):
    calls: list[str] = []

    def fake_run(args, **kw):
        calls.append(kw["input"])
        return _proc()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    first = llm.call_json("sys", "user", SCHEMA, session="s1")
    second = llm.call_json("sys", "user", SCHEMA, session="s1")

    assert len(calls) == 1  # no LLM call for the repeat
    assert first == {"x": 5}
    assert second == {"x": 5, "cached": True}


def test_cache_marker_is_not_persisted(monkeypatch):
    """The stored entry is the raw result: hits keep marking cached: true on every replay."""
    monkeypatch.setattr(llm.subprocess, "run", lambda args, **kw: _proc())
    llm.call_json("sys", "user", SCHEMA, session="s1")
    for _row in llm._session_cache.values():
        assert "cached" not in _row[1]
    assert llm.call_json("sys", "user", SCHEMA, session="s1")["cached"] is True


def test_different_session_or_question_misses(monkeypatch):
    calls: list[str] = []

    def fake_run(args, **kw):
        calls.append(kw["input"])
        return _proc()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm.call_json("sys", "user", SCHEMA, session="s1")
    llm.call_json("sys", "user", SCHEMA, session="s2")  # other session
    llm.call_json("sys", "other question", SCHEMA, session="s1")  # same session, new text
    assert len(calls) == 3


def test_reset_session_cache_forces_recall(monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(kw["input"])
        return _proc()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm.call_json("sys", "user", SCHEMA, session="s1")
    llm.reset_session_cache()
    llm.call_json("sys", "user", SCHEMA, session="s1")
    assert len(calls) == 2


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("MYCELIUM_SESSION_CACHE", "0")
    calls = []

    def fake_run(args, **kw):
        calls.append(kw["input"])
        return _proc()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm.call_json("sys", "user", SCHEMA, session="s1")
    llm.call_json("sys", "user", SCHEMA, session="s1")
    assert len(calls) == 2


def test_no_session_param_never_caches(monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(kw["input"])
        return _proc()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm.call_json("sys", "user", SCHEMA)
    llm.call_json("sys", "user", SCHEMA)
    assert len(calls) == 2


def test_ttl_expiry(monkeypatch):
    monkeypatch.setenv("MYCELIUM_SESSION_CACHE_TTL_S", "0")  # everything is immediately expired
    calls = []

    def fake_run(args, **kw):
        calls.append(kw["input"])
        return _proc()

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm.call_json("sys", "user", SCHEMA, session="s1")
    llm.call_json("sys", "user", SCHEMA, session="s1")
    assert len(calls) == 2


def test_hit_returns_a_copy(monkeypatch):
    """Mutating a hit must not poison the cache for the next caller."""
    monkeypatch.setattr(llm.subprocess, "run", lambda args, **kw: _proc())
    first = llm.call_json("sys", "user", SCHEMA, session="s1")
    second = llm.call_json("sys", "user", SCHEMA, session="s1")
    second["x"] = 999
    assert first["x"] == 5
    assert llm.call_json("sys", "user", SCHEMA, session="s1")["x"] == 5
