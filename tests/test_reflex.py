"""Reflex: shape cascade, prior-weighted selection, citation stripping, spoken tier, full ask() with fakes."""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types

import pytest

import core
from core.contracts import CONFIG, Citation


def _ensure(name: str, **attrs) -> types.ModuleType:
    """Import core.<name>; if a sibling module is not written yet, install a stub so tests can patch it."""
    full = f"core.{name}"
    try:
        mod = importlib.import_module(full)
    except Exception:
        mod = types.ModuleType(full)
        sys.modules[full] = mod
        setattr(core, name, mod)
    for k, v in attrs.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)
    return mod


class _LLMError(Exception):
    pass


def _stub_call_json(*a, **k):
    raise AssertionError("real call_json must not run in unit tests")


_ensure("llm", LLMError=_LLMError, call_json=_stub_call_json)
_ensure("retrieve")
_ensure("learn")
_ensure("growth")

from core import reflex  # noqa: E402


def test_classify_shape_cascade():
    assert reflex.classify_shape("who owns the budget") == "who-owns"
    assert reflex.classify_shape("what is a retainer") == "definition"
    assert reflex.classify_shape("how do I file expenses") == "how-to"
    assert reflex.classify_shape("is remote work allowed") == "policy"
    assert reflex.classify_shape("what's the deadline for reviews") == "when"
    assert reflex.classify_shape("compare plan A versus plan B") == "comparison"
    assert reflex.classify_shape("office address") == "factual"


def test_select_reading_uses_prior():
    rs = [
        {"role": "literal", "paraphrase": "a", "p": 1 / 3},
        {"role": "intent", "paraphrase": "b", "p": 1 / 3},
        {"role": "scope", "paraphrase": "c", "p": 1 / 3},
    ]
    taken, post, h = reflex.select_reading(rs, [0.6, 0.2, 0.2])
    assert taken["role"] == "literal"
    assert abs(sum(post) - 1) < 1e-9
    assert post[0] == pytest.approx(0.6)
    assert h < 1.585


def test_strip_unresolved_drops_dangling():
    result = {
        "spoken": "Alice owns it [1] [5].",
        "screen": "Alice owns the budget [1][5].",
        "claims": [{"text": "Alice owns the budget", "chunk_ids": [1, 5]}],
    }
    out, resolved, total = reflex.strip_unresolved(result, 2)
    assert out["claims"][0]["chunk_ids"] == [1]
    assert "[5]" not in out["screen"]
    assert "[1]" in out["screen"]
    assert "[5]" not in out["spoken"]
    assert (resolved, total) == (1, 2)


def test_spoken_tier_word_cap_and_suffix():
    long = " ".join(f"Sentence number {i} says something useful [1]." for i in range(30))
    taken = {"role": "literal", "paraphrase": "who owns the budget"}
    s = reflex.spoken_tier({"spoken": long}, taken)
    assert len(s.split()) <= CONFIG["spoken_max_words"]
    assert s.endswith("I read that as who owns the budget.")
    assert "[" not in s and "]" not in s


def test_spoken_tier_short_keeps_text():
    taken = {"role": "intent", "paraphrase": "the budget owner"}
    s = reflex.spoken_tier({"spoken": "Alice owns it [2]."}, taken)
    assert s == "Alice owns it. I read that as the budget owner."


def test_ask_pipeline_with_fakes(fake_llm, fake_db, monkeypatch):
    fake_llm.push(
        {
            "shape": "who-owns",
            "readings": [
                {"role": "literal", "paraphrase": "who owns the budget", "assumption": "", "query": "budget owner", "p": 2},
                {"role": "intent", "paraphrase": "who approves spend", "assumption": "", "query": "approve spend", "p": 1},
                {"role": "scope", "paraphrase": "which team budget", "assumption": "", "query": "team budget", "p": 1},
            ],
            "taken": "literal",
            "why": "most direct",
            "flip_condition": "if asking about approvals",
        },
        {
            "spoken": "Finance owns the budget [1].",
            "screen": "Finance owns the budget [1]. Approvals go to the CFO [2].",
            "claims": [
                {"text": "Finance owns the budget", "chunk_ids": [1]},
                {"text": "Approvals go to the CFO", "chunk_ids": [2]},
            ],
            "contradictions": [],
            "gaps": [],
            "confidence": "high",
        },
    )
    cites = [
        Citation(1, "chunk:a", "Budget", "http://x/a", "Finance owns", 0.9),
        Citation(2, "chunk:b", "Approvals", "http://x/b", "CFO approves", 0.8),
    ]
    seen = {}

    def fake_retrieve(text, k=8):
        seen["query"] = text
        return "[1] Budget\n[2] Approvals", cites, []

    async def fake_prior(shape):
        return [0.5, 0.25, 0.25]

    recorded = {}

    async def fake_record(question_id, shape, taken_role, chunk_ids):
        recorded.update(qid=question_id, shape=shape, role=taken_role, chunks=chunk_ids)

    monkeypatch.setattr(core.retrieve, "retrieve", fake_retrieve, raising=False)
    monkeypatch.setattr(core.learn, "get_prior", fake_prior, raising=False)
    monkeypatch.setattr(core.learn, "record_answer", fake_record, raising=False)
    monkeypatch.setattr(core.growth, "load_slot", lambda: "SLOT", raising=False)
    monkeypatch.setattr(core.growth, "render_prompt", lambda s, c, q: f"{s}|{c}|{q}|CONTRACT", raising=False)
    monkeypatch.setattr(core.growth, "slot_version", lambda: "1.0", raising=False)
    fake_db.rows["CREATE"] = [{"id": "question:abc"}]

    out = asyncio.run(reflex.ask("who owns the budget", "sess1"))

    for key in (
        "question_id", "shape", "readings", "taken", "taken_reading", "why", "flip_condition", "spoken",
        "screen", "citations", "citations_resolved", "prior", "entropy_bits", "slot_version", "ms", "session_id",
    ):
        assert key in out, key
    assert out["citations_resolved"] == "2/2"
    assert out["taken"] == "literal"
    assert out["question_id"] == "question:abc"
    assert seen["query"] == "budget owner"
    assert abs(sum(r["p"] for r in out["readings"]) - 1) < 1e-9
    assert all({"role", "paraphrase", "p", "posterior"} <= set(r) for r in out["readings"])
    assert out["spoken"].endswith("I read that as who owns the budget.")
    assert "[" not in out["spoken"]
    assert recorded == {"qid": "question:abc", "shape": "who-owns", "role": "literal", "chunks": ["chunk:a", "chunk:b"]}
    assert fake_llm.calls[1]["system"] == "SLOT|[1] Budget\n[2] Approvals|who owns the budget|CONTRACT"
    json.dumps(out)
    assert any("question" in s for s in fake_db.sql())


def test_readings_fallback_on_llm_error(fake_llm):
    from core import llm

    fake_llm.push(llm.LLMError("boom"))
    r = asyncio.run(reflex.readings("who owns the budget"))
    assert r["shape"] == "who-owns"
    assert len(r["readings"]) == 1 and r["readings"][0]["role"] == "literal" and r["readings"][0]["p"] == 1.0


def test_synthesize_fallback_on_llm_error(fake_llm, monkeypatch):
    from core import llm

    monkeypatch.setattr(core.growth, "render_prompt", lambda s, c, q: "SYS", raising=False)
    fake_llm.push(llm.LLMError("boom"))
    r = asyncio.run(reflex.synthesize("q", "[1] Budget Page\nbody", "SLOT"))
    assert r["spoken"] == "I found passages but could not compose an answer."
    assert r["claims"] == []
