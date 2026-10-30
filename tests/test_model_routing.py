"""Feature 27: model routing — readings carry the cheap-model env, synthesis carries Opus."""
from __future__ import annotations

import subprocess

import pytest

from core import llm, reflex

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
CHEAP = "us.anthropic.claude-haiku-test"


def _proc() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["claude"], returncode=0, stdout='{"result": "{\\"x\\": 1}"}', stderr="")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("MYCELIUM_CHEAP_MODEL", raising=False)
    monkeypatch.delenv("MYCELIUM_MODEL_DAEMON", raising=False)
    llm.reset_session_cache()
    yield
    llm.reset_session_cache()


@pytest.fixture
def routed_runs(monkeypatch) -> list[dict | None]:
    """Capture the env each cold CLI attempt receives; short-circuit with a valid proc."""
    runs: list[dict | None] = []

    def fake_run(args, user, timeout_s, bucket, env):
        runs.append(env)
        return _proc()

    monkeypatch.setattr(llm, "_run", fake_run)
    return runs


def test_routing_off_by_default(routed_runs):
    assert llm.routing_enabled() is False
    assert llm.route_env("readings") is None
    llm.call_json("sys", "q-off", SCHEMA, role="readings")
    assert routed_runs[0] is None  # env untouched: today's behavior


def test_readings_carry_cheap_model_env(monkeypatch, routed_runs):
    monkeypatch.setenv("MYCELIUM_CHEAP_MODEL", CHEAP)
    llm.call_json("sys", "q-r", SCHEMA, role="readings")
    assert routed_runs[0]["ANTHROPIC_MODEL"] == CHEAP


def test_synthesis_carries_opus(monkeypatch, routed_runs):
    monkeypatch.setenv("MYCELIUM_CHEAP_MODEL", CHEAP)
    llm.call_json("sys", "q-s", SCHEMA, role="synthesis")
    assert routed_runs[0]["ANTHROPIC_MODEL"] == llm.OPUS_MODEL_ID


def test_unrouted_roles_stay_on_host_default(monkeypatch, routed_runs):
    monkeypatch.setenv("MYCELIUM_CHEAP_MODEL", CHEAP)
    assert llm.route_env("reflect") is None
    assert llm.route_env(None) is None
    llm.call_json("sys", "q-u", SCHEMA, role="reflect")
    assert routed_runs[0] is None


def test_routed_call_bypasses_daemon(monkeypatch, routed_runs):
    monkeypatch.setenv("MYCELIUM_CHEAP_MODEL", CHEAP)
    monkeypatch.setenv("MYCELIUM_MODEL_DAEMON", "1")

    def boom(*a, **k):
        raise AssertionError("the warm daemon must not serve routed calls")

    monkeypatch.setattr(llm, "_daemon", boom)
    llm.call_json("sys", "q-d", SCHEMA, role="readings")
    assert routed_runs and routed_runs[0]["ANTHROPIC_MODEL"] == CHEAP


def test_routed_roles_cached_separately(monkeypatch, routed_runs):
    # routing changes the model, so a routed call must not serve an unrouted session-cache entry
    monkeypatch.setenv("MYCELIUM_CHEAP_MODEL", CHEAP)
    llm.call_json("sys", "same-q", SCHEMA, role="readings")
    llm.call_json("sys", "same-q", SCHEMA, role="synthesis")
    assert len(routed_runs) == 2


async def test_reflex_readings_routes_readings(fake_llm):
    fake_llm.push({"shape": "factual",
                   "readings": [{"role": "literal", "paraphrase": "q", "assumption": "", "query": "q", "p": 1.0}],
                   "taken": "literal", "why": "w", "flip_condition": "f"})
    await reflex.readings("what is x?")
    assert fake_llm.calls[0]["role"] == "readings"


async def test_reflex_synthesis_routes_synthesis(fake_llm):
    fake_llm.push({"spoken": "s", "screen": "c", "claims": [], "contradictions": [], "gaps": [],
                   "confidence": "high"})
    await reflex.synthesize("q", "ctx", "slot text")
    assert fake_llm.calls[0]["role"] == "synthesis"
