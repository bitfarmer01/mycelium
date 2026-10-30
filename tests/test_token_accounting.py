"""Token/cost accounting (feature 23): CLI-JSON usage lands in the caller's dict, the answer object, and the ledger."""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from core import llm, reflex

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}

USAGE = {"input_tokens": 12, "output_tokens": 34, "cache_read_input_tokens": 5}


def _envelope(result: dict, usage: dict | None) -> str:
    return json.dumps({"result": json.dumps(result), "usage": usage})


def _proc(stdout: str, rc: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=["claude"], returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _usage_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE_LOG", tmp_path / "usage.jsonl")
    monkeypatch.setattr(llm, "daemon_enabled", lambda: False)


def test_usage_lands_in_caller_dict(monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run", lambda args, **kw: _proc(_envelope({"x": 1}, USAGE)))
    acc: dict = {}
    assert llm.call_json("sys", "user", SCHEMA, usage=acc) == {"x": 1}
    assert acc["input_tokens"] == 12
    assert acc["output_tokens"] == 34
    assert acc["cache_read_input_tokens"] == 5
    assert acc["attempts"] == 1


def test_usage_sums_across_retry_attempts(monkeypatch):
    outs = [_proc(_envelope({"x": "nope"}, USAGE)), _proc(_envelope({"x": 5}, USAGE))]
    monkeypatch.setattr(llm.subprocess, "run", lambda args, **kw: outs.pop(0))
    acc: dict = {}
    assert llm.call_json("sys", "user", SCHEMA, usage=acc, retries=1) == {"x": 5}
    assert acc["input_tokens"] == 24  # both attempts burned tokens
    assert acc["output_tokens"] == 68
    assert acc["attempts"] == 2


def test_usage_counts_attempts_that_fail_validation(monkeypatch):
    """Every attempt costs money, including the final one that never validates."""
    monkeypatch.setattr(llm.subprocess, "run", lambda args, **kw: _proc(_envelope({"x": "nope"}, USAGE)))
    acc: dict = {}
    with pytest.raises(llm.LLMError):
        llm.call_json("sys", "user", SCHEMA, usage=acc, retries=1)
    assert acc["attempts"] == 2
    assert acc["input_tokens"] == 24


def test_usage_none_when_envelope_has_none(monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run", lambda args, **kw: _proc(_envelope({"x": 1}, None)))
    acc: dict = {}
    llm.call_json("sys", "user", SCHEMA, usage=acc)
    assert acc == {"attempts": 1}


def test_call_text_usage(monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run", lambda args, **kw: _proc(_envelope({"x": 1}, USAGE)))
    acc: dict = {}
    assert llm.call_text("sys", "user", usage=acc)
    assert acc["input_tokens"] == 12 and acc["attempts"] == 1


def test_ask_lands_usage_in_answer_and_ledger(fake_llm, fake_db, monkeypatch):
    """End to end with fakes: the answer object carries usage and the ledger records a usage row."""
    fake_llm.push(
        {  # readings
            "shape": "factual",
            "readings": [{"role": "literal", "paraphrase": "q", "assumption": "", "query": "q", "p": 1}],
            "taken": "literal", "why": "", "flip_condition": "",
        },
        {"spoken": "Answer [1].", "screen": "Answer [1].", "claims": [{"text": "Answer", "chunk_ids": [1]}],
         "contradictions": [], "gaps": [], "confidence": "high"},  # synthesis
    )

    def usage_aware_call_json(system, user, schema=None, **kw):
        out = fake_llm(system, user, schema, **kw)
        acc = kw.get("usage")
        if isinstance(acc, dict):  # sums, mirroring the real _merge_usage contract
            acc["input_tokens"] = acc.get("input_tokens", 0) + 7
            acc["output_tokens"] = acc.get("output_tokens", 0) + 11
            acc["attempts"] = acc.get("attempts", 0) + 1
        return out

    monkeypatch.setattr("core.llm.call_json", usage_aware_call_json)

    ledger_rows: list = []

    async def fake_write_ledger(row):
        ledger_rows.append(row)
        return "ledger:1"

    from core import learn

    monkeypatch.setattr(learn, "write_ledger", fake_write_ledger, raising=False)
    monkeypatch.setattr("core.retrieve.retrieve", lambda text, k=8, reading=None: ("", [], []), raising=False)
    monkeypatch.setattr("core.learn.get_prior", lambda shape: _prior(), raising=False)
    async def fake_record(*a, **k):
        return None

    monkeypatch.setattr("core.learn.record_answer", fake_record, raising=False)
    monkeypatch.setattr("core.growth.load_slot", lambda: "SLOT", raising=False)
    monkeypatch.setattr("core.growth.render_prompt", lambda s, c, q: "SYSTEM", raising=False)
    monkeypatch.setattr("core.growth.slot_version", lambda: "1.0", raising=False)
    fake_db.rows["CREATE"] = [{"id": "question:abc"}]

    out = asyncio.run(reflex.ask("what is this", "sess-usage"))

    assert out["usage"]["input_tokens"] == 14  # readings + synthesis
    assert out["usage"]["output_tokens"] == 22
    assert out["usage"]["attempts"] == 2
    assert out["cached"] is False
    assert len(ledger_rows) == 1
    row = ledger_rows[0].to_dict()
    assert row["type"] == "usage"
    assert row["target"] == "question:abc"
    assert row["after"] == 36.0  # 14 in + 22 out


def _prior():
    return [1 / 3, 1 / 3, 1 / 3]
