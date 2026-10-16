"""Unit tests for core.llm (no real CLI calls: subprocess.run is monkeypatched)."""
from __future__ import annotations

import json
import subprocess

import pytest

import core.llm as llm

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def _proc(stdout: str, rc: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=["claude"], returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _usage_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE_LOG", tmp_path / "usage.jsonl")


def test_parse_result_fenced():
    out = json.dumps({"result": "Sure:\n```json\n{\"x\": 3}\n```\nDone."})
    assert llm.parse_result(out) == {"x": 3}


def test_parse_result_prefers_structured_output():
    out = json.dumps({"result": "", "structured_output": {"x": 7}})
    assert llm.parse_result(out) == {"x": 7}


def test_parse_result_garbage_raises():
    with pytest.raises(llm.LLMError):
        llm.parse_result(json.dumps({"result": "no json here"}))


def test_call_json_retries_on_schema_error(monkeypatch):
    outs = [_proc(json.dumps({"result": '{"x": "nope"}'})), _proc(json.dumps({"result": '{"x": 5}'}))]
    calls = []

    def fake_run(args, **kw):
        calls.append(kw["input"])
        return outs.pop(0)

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.call_json("sys", "user", SCHEMA) == {"x": 5}
    assert len(calls) == 2
    assert "did not conform" in calls[1]


def test_timeout_raises_llmerror(monkeypatch):
    def fake_run(args, **kw):
        raise subprocess.TimeoutExpired(args, kw.get("timeout"))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    with pytest.raises(llm.LLMError):
        llm.call_text("sys", "user", timeout_s=0.1)


def test_unknown_option_falls_back_without_json_schema(monkeypatch):
    seen = []

    def fake_run(args, **kw):
        seen.append(list(args))
        if "--json-schema" in args:
            return _proc("", rc=1, stderr="error: unknown option '--json-schema'")
        return _proc(json.dumps({"result": '{"x": 1}'}))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.call_json("sys", "u", SCHEMA) == {"x": 1}
    assert "--json-schema" not in seen[-1]
    assert '"required"' in seen[-1][seen[-1].index("--system-prompt") + 1]


def test_bedrock_env_has_three_keys(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    env = llm.bedrock_env()
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["AWS_REGION"] == "us-west-2"
    assert env["ANTHROPIC_MODEL"] == "us.anthropic.claude-opus-4-5-20251101-v1:0"


def test_usage_log_has_no_env_values(monkeypatch):
    monkeypatch.setenv("SECRET_THING", "sekrit-value-123")
    monkeypatch.setattr(llm.subprocess, "run", lambda a, **k: _proc(json.dumps({"result": "OK", "usage": {"input_tokens": 1}})))
    assert llm.call_text("s", "u") == "OK"
    text = llm.USAGE_LOG.read_text()
    assert "sekrit-value-123" not in text
    row = json.loads(text.splitlines()[-1])
    assert set(row) == {"ts", "bucket", "ms", "usage", "ok"} and row["ok"] is True
