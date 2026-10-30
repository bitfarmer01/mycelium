"""Schema-retry with the validation error fed back (feature 24): exactly the configured retries, error in the re-prompt."""
from __future__ import annotations

import json
import subprocess

import pytest

from core import llm

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
BAD = json.dumps({"result": '{"x": "nope"}'})
GOOD = json.dumps({"result": '{"x": 5}'})


def _proc(stdout: str, rc: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=["claude"], returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _no_daemon(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "daemon_enabled", lambda: False)
    monkeypatch.setattr(llm, "USAGE_LOG", tmp_path / "usage.jsonl")


def test_exactly_one_retry_and_error_in_prompt(monkeypatch):
    """Invalid first response with retries=1: two calls total, the re-prompt names the validation error."""
    outs = [_proc(BAD), _proc(GOOD)]
    prompts: list[str] = []

    def fake_run(args, **kw):
        prompts.append(kw["input"])
        return outs.pop(0)

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.call_json("sys", "user", SCHEMA, retries=1) == {"x": 5}
    assert len(prompts) == 2
    assert "did not conform" in prompts[1]
    assert "'nope' is not of type 'integer'" in prompts[1]  # the exact jsonschema message, not a summary


def test_retries_zero_fails_after_single_call(monkeypatch):
    calls: list[str] = []

    def fake_run(args, **kw):
        calls.append(kw["input"])
        return _proc(BAD)

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    with pytest.raises(llm.LLMError, match="schema validation failed after 1 attempts"):
        llm.call_json("sys", "user", SCHEMA, retries=0)
    assert len(calls) == 1


def test_converges_after_two_bad_responses(monkeypatch):
    outs = [_proc(BAD), _proc(_fenced()), _proc(GOOD)]
    prompts: list[str] = []

    def fake_run(args, **kw):
        prompts.append(kw["input"])
        return outs.pop(0)

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.call_json("sys", "user", SCHEMA, retries=2) == {"x": 5}
    assert len(prompts) == 3
    assert "did not conform" in prompts[1] and "did not conform" in prompts[2]


def _fenced() -> str:
    return json.dumps({"result": "Here you go:\n```json\nnot an object\n```"})


def test_unparseable_result_also_retries_with_error(monkeypatch):
    outs = [_proc(json.dumps({"result": "no json anywhere"})), _proc(GOOD)]
    prompts: list[str] = []

    def fake_run(args, **kw):
        prompts.append(kw["input"])
        return outs.pop(0)

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.call_json("sys", "user", SCHEMA, retries=1) == {"x": 5}
    assert "did not conform" in prompts[1]
    assert "no JSON object in model result" in prompts[1]
