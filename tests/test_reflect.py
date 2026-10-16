"""Tests for core.reflect: validation, zero-row path, LLM-error fallback, fallback cap, derivation."""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

# core.llm (Task 2) and core.learn (Task 5) are built in parallel; stub them if absent.
try:
    import core.llm  # noqa: F401
except ImportError:
    _llm = types.ModuleType("core.llm")

    class LLMError(Exception):
        pass

    def call_json(system, user, schema=None, **kw):  # pragma: no cover
        raise AssertionError("real call_json must not run in tests")

    _llm.LLMError = LLMError
    _llm.call_json = call_json
    sys.modules["core.llm"] = _llm
    import core

    core.llm = _llm

try:
    import core.learn  # noqa: F401
except ImportError:
    _learn = types.ModuleType("core.learn")

    async def write_lesson(*a, **kw):  # pragma: no cover
        raise AssertionError("stub write_lesson should be patched")

    _learn.write_lesson = write_lesson
    sys.modules["core.learn"] = _learn
    import core

    core.learn = _learn

from core import llm, reflect  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _lessons(n):
    return [
        {"id": f"lesson:l{i}", "phase": "learn", "change": f"change {i}", "rationale": f"why {i}",
         "contributor": "test", "ts": "2026-10-16T01:00:00+00:00", "source_ids": [], "observation_ids": [],
         "kind": "lesson"}
        for i in range(n)
    ]


def test_validate_drops_unknown_ids():
    sentences = [
        {"text": "good", "row_ids": ["lesson:a"]},
        {"text": "unknown", "row_ids": ["lesson:zzz"]},
        {"text": "mixed", "row_ids": ["lesson:a", "lesson:nope"]},
        {"text": "uncited", "row_ids": []},
    ]
    kept, dropped = reflect.validate_sentences(sentences, {"lesson:a"})
    assert kept == [{"text": "good", "row_ids": ["lesson:a"]}]
    assert dropped == 3


def test_reflect_zero_rows_no_call(fake_db, fake_llm):
    fake_db.default = []
    out = run(reflect.reflect())
    assert out["rows_read"] == 0
    assert out["llm"] is False
    assert out["dropped"] == 0
    assert out["sentences"] == [{"text": "I have not learned anything yet today.", "row_ids": []}]
    assert fake_llm.calls == []


def test_reflect_uses_fallback_on_llm_error(fake_db, fake_llm):
    rows = _lessons(2)
    fake_db.rows["FROM lesson WHERE"] = rows
    fake_llm.push(llm.LLMError("boom"))
    out = run(reflect.reflect())
    assert len(fake_llm.calls) == 1
    assert out["llm"] is False
    assert out["rows_read"] == 2
    assert out["sentences"] == reflect.render_fallback(rows)
    assert out["sentences"][0] == {"text": "change 0: why 0", "row_ids": ["lesson:l0"]}
    assert "change 0: why 0" in out["spoken"]


def test_reflect_validates_llm_output(fake_db, fake_llm):
    rows = _lessons(2)
    fake_db.rows["FROM lesson WHERE"] = rows
    fake_llm.push({"sentences": [
        {"text": "Lesson zero was written.", "row_ids": ["lesson:l0"]},
        {"text": "Invented.", "row_ids": ["lesson:ghost"]},
    ]})
    out = run(reflect.reflect())
    assert out["llm"] is True
    assert out["dropped"] == 1
    assert out["sentences"] == [{"text": "Lesson zero was written.", "row_ids": ["lesson:l0"]}]
    assert out["spoken"] == "Lesson zero was written."


def test_reflect_all_dropped_uses_fallback(fake_db, fake_llm):
    rows = _lessons(1)
    fake_db.rows["FROM lesson WHERE"] = rows
    fake_llm.push({"sentences": [{"text": "Invented.", "row_ids": ["lesson:ghost"]}]})
    out = run(reflect.reflect())
    assert out["dropped"] == 1
    assert out["sentences"] == reflect.render_fallback(rows)


def test_render_fallback_caps_at_five():
    out = reflect.render_fallback(_lessons(8))
    assert len(out) == 5
    assert all(len(s["row_ids"]) == 1 for s in out)


def test_derive_writes_lessons_for_unreferenced(fake_db, monkeypatch):
    fake_db.rows["FROM lesson"] = [{"id": "lesson:x", "observation_ids": ["observation:seen"], "source_ids": []}]
    fake_db.rows["FROM observation"] = [
        {"id": "observation:seen", "kind": "correct", "from_role": "literal", "to_role": "intent"},
        {"id": "observation:new", "kind": "correct", "from_role": "literal", "to_role": "scope", "question_id": "q1"},
    ]
    fake_db.rows["FROM candidate"] = [{"id": "candidate:c1", "name": "Notion", "axis": "source",
                                       "status": "vetoed", "veto_reason": "stale", "verdict": None}]
    written = []

    async def fake_write(phase, change, rationale, contributor, observation_ids, source_ids, kind):
        written.append({"phase": phase, "observation_ids": observation_ids, "source_ids": source_ids, "kind": kind})
        return "lesson:new"

    import core.learn

    monkeypatch.setattr(core.learn, "write_lesson", fake_write)
    n = run(reflect.derive_lessons_from_observations())
    assert n == 2
    assert written[0]["observation_ids"] == ["observation:new"]
    assert written[1]["source_ids"] == ["candidate:c1"]
