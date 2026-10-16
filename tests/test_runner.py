"""Runner: exam script compiles, LocalRunner collects answers (subprocess stubbed), make_runner falls back."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import types

import pytest

from core import runner


@pytest.fixture(autouse=True)
def _stub_growth(monkeypatch):
    g = types.ModuleType("core.growth")
    g.render_prompt = lambda slot, context, question: f"{slot}\n{context}\n{question}"
    g.load_slot = lambda: "SLOT {context} {question}"
    g.score_answer = lambda result, terms, n_docs: 1.0
    monkeypatch.setitem(sys.modules, "core.growth", g)
    import core
    monkeypatch.setattr(core, "growth", g, raising=False)


def test_exam_script_is_valid_python():
    compile(runner.exam_script(), "exam", "exec")


def test_local_runner_collects_answers(monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        out = {"structured_output": {"spoken": "ok", "screen": "ok [1]", "claims": [], "contradictions": [],
                                     "gaps": [], "confidence": "high"}}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(out), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    packs = [{"question_id": "q1", "text": "who?", "context": "[1] a", "n_docs": 1, "chunk_ids": ["chunk:a"]},
             {"question_id": "q2", "text": "how?", "context": "[1] b", "n_docs": 1, "chunk_ids": ["chunk:b"]}]
    res = asyncio.run(runner.LocalRunner().run("SLOT", packs, "t"))
    assert res["runner"] == "local"
    assert "sandbox_id" in res
    assert [a["question_id"] for a in res["answers"]] == ["q1", "q2"]
    assert all(a["result"] and a["result"]["spoken"] == "ok" for a in res["answers"])
    assert len(calls) == 2 and calls[0][0] == "claude"


def test_make_runner_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.setattr(runner, "bedrock_health", lambda: True)
    assert isinstance(runner.make_runner(), runner.LocalRunner)
