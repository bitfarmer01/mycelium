"""Feature 22: warm model daemon — lifecycle, spawn shape, context isolation (no real CLI).

The fake CLI is a real pair of os pipes with a child thread, so the daemon's reader thread, event
queue, and kill paths run against genuine blocking reads. The stream-json protocol follows the
documented Claude Code CLI interface; the daemon treats every protocol failure as cold-path fallback,
so a CLI build that cannot serve stream-json degrades to today's behavior instead of breaking answers.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from typing import ClassVar

import pytest

from core import llm

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
SYS = "readings system prompt"


class FakeDaemonCLI:
    """Stand-in for the claude process: real pipes, blocking reads, one scripted answer per message."""

    instances: ClassVar[list[FakeDaemonCLI]] = []

    def __init__(self, args, startup_s: float = 0.0, hang: bool = False, die_immediately: bool = False):
        self.args = list(args)
        self.hang = hang
        self.received: list[str] = []
        self.killed = False
        FakeDaemonCLI.instances.append(self)
        r_in, w_in = os.pipe()
        r_out, w_out = os.pipe()
        self.stdin = os.fdopen(w_in, "w", buffering=1)
        self.stdout = os.fdopen(r_out)
        self._child_r = os.fdopen(r_in)
        self._child_w = os.fdopen(w_out, "w", buffering=1)
        if die_immediately:
            self._child_w.close()  # process died at startup: parent's reader sees EOF immediately
            return
        threading.Thread(target=self._serve, args=(startup_s,), daemon=True).start()

    def _serve(self, startup_s: float) -> None:
        time.sleep(startup_s)  # CLI startup runs while other work happens, never while a caller waits
        try:
            while True:
                line = self._child_r.readline()
                if not line:
                    break
                self.received.append(line)
                if self.hang:
                    continue
                msg = json.loads(line)
                text = msg["message"]["content"][0]["text"]
                ev = {"type": "result", "subtype": "success", "session_id": "fake", "result": "",
                      "structured_output": {"answer": f"daemon:{text}"},
                      "usage": {"input_tokens": 1, "output_tokens": 1}}
                self._child_w.write(json.dumps(ev) + "\n")
                self._child_w.flush()
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
            try:
                self._child_w.close()  # death mid-life: parent's reader must see EOF
            except OSError:
                pass
        finally:
            try:
                self._child_w.close()
            except OSError:
                pass

    def kill(self):
        self.killed = True
        for f in (self.stdin, self._child_r, self._child_w):
            try:
                f.close()
            except OSError:
                pass


def _cold_ok(monkeypatch, prefix: str = "cold", sleep_s: float = 0.0) -> list[str]:
    """Cold CLI path under test: always answers, derived from the prompt. Returns the recorded inputs."""
    cold_calls: list[str] = []

    def fake_run(args, input=None, **kw):
        cold_calls.append(input)
        if sleep_s:
            time.sleep(sleep_s)
        return subprocess.CompletedProcess(args=["claude"], returncode=0, stderr="",
                                           stdout=json.dumps({"result": "",
                                                              "structured_output": {"answer": f"{prefix}:{input}"}}))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    return cold_calls


def _wait_for(pred, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


@pytest.fixture(autouse=True)
def _clean_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE_LOG", tmp_path / "usage.jsonl")
    llm.reset_daemon()
    yield
    llm.reset_daemon()
    for fake in FakeDaemonCLI.instances:
        for f in (fake.stdin, fake.stdout, fake._child_r, fake._child_w):
            try:
                f.close()
            except (OSError, ValueError):
                pass
    FakeDaemonCLI.instances.clear()


def test_daemon_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MYCELIUM_MODEL_DAEMON", raising=False)

    def boom(*a, **kw):
        raise AssertionError("Popen must never run when the daemon is disabled")

    monkeypatch.setattr(llm.subprocess, "Popen", boom)
    cold_calls = _cold_ok(monkeypatch)

    assert llm.daemon_enabled() is False
    assert llm.call_json(SYS, "q", SCHEMA) == {"answer": "cold:q"}
    assert len(cold_calls) == 1  # identical to pre-daemon behavior


def test_daemon_args_mirror_cold_cli_flags(monkeypatch):
    captured: list[list[str]] = []

    def fake_popen(args, **kw):
        captured.append(list(args))
        return FakeDaemonCLI(args, die_immediately=True)

    monkeypatch.setattr(llm.subprocess, "Popen", fake_popen)
    d = llm.ModelDaemon()
    d._maybe_prewarm(d.key(SYS, SCHEMA), SYS, SCHEMA)
    assert _wait_for(lambda: d.pending_spawns() == 0), "spawn never completed"

    warm, cold = captured[0], llm.cli_args(SYS, SCHEMA)
    assert warm[:2] == cold[:2] == ["claude", "--print"]
    for flag in ("--max-turns", "--tools", "--strict-mcp-config", "--no-session-persistence", "--system-prompt"):
        assert warm[warm.index(flag) + 1] == cold[cold.index(flag) + 1], flag
    assert warm[warm.index("--input-format") + 1] == "stream-json"
    assert warm[warm.index("--output-format") + 1] == "stream-json"
    assert "--json-schema" in warm and warm[warm.index("--json-schema") + 1] == json.dumps(SCHEMA)


def test_warm_daemon_answers_twenty_questions_without_per_call_startup(monkeypatch):
    monkeypatch.setenv("MYCELIUM_MODEL_DAEMON", "1")
    fakes: list[FakeDaemonCLI] = []

    def fake_popen(args, **kw):
        fake = FakeDaemonCLI(args, startup_s=0.02)  # CLI startup paid in the background
        fakes.append(fake)
        return fake

    monkeypatch.setattr(llm.subprocess, "Popen", fake_popen)
    cold_calls = _cold_ok(monkeypatch, sleep_s=0.3)  # cold path pays startup + think serially

    # two cold calls teach the daemon this key repeats; the warm process lands during the second
    assert llm.call_json(SYS, "q0", SCHEMA) == {"answer": "cold:q0"}
    assert llm.call_json(SYS, "q1", SCHEMA) == {"answer": "cold:q1"}
    assert _wait_for(lambda: llm._daemon().warm_keys()), "no warm process after two uses of one key"

    answers = []
    for i in range(2, 22):  # 20 consecutive questions
        answers.append(llm.call_json(SYS, f"q{i}", SCHEMA))
        time.sleep(0.1)  # retrieval + ledger time between model calls; replacement spawn (0.02s) hides inside

    assert answers == [{"answer": f"daemon:q{i}"} for i in range(2, 22)]  # daemon answered, in order
    assert len(cold_calls) == 2  # after warm-up: zero per-call CLI startups on the answer path
    assert len(fakes) == 21  # 1 warm process after warm-up + 1 background replacement per warm call
    assert all(len(f.received) <= 1 for f in fakes)  # one question per process: no shared conversation


def test_idle_ttl_reaps_unused_warm_process():
    d = llm.ModelDaemon(idle_ttl_s=0.0)
    killed: list[int] = []

    class _DeadProc:
        def kill(self):
            killed.append(1)

    stale = llm._DaemonProc(proc=_DeadProc(), events=queue.Queue(), spawned_at=time.monotonic() - 1)
    with d._lock:
        d._warm[d.key(SYS, SCHEMA)] = stale

    with pytest.raises(llm.DaemonUnavailable):
        d.run(SYS, SCHEMA, "q", timeout_s=1, bucket="voice")
    assert killed == [1]  # the stale process was reaped, not served


def test_dead_daemon_process_falls_back_to_cold_and_cools_down(monkeypatch, caplog):
    monkeypatch.setenv("MYCELIUM_MODEL_DAEMON", "1")
    spawns: list[FakeDaemonCLI] = []

    def fake_popen(args, **kw):
        fake = FakeDaemonCLI(args, die_immediately=True)  # CLI cannot serve stream-json: dies at startup
        spawns.append(fake)
        return fake

    monkeypatch.setattr(llm.subprocess, "Popen", fake_popen)
    _cold_ok(monkeypatch)

    key = llm.ModelDaemon.key(SYS, SCHEMA)
    assert llm.call_json(SYS, "q0", SCHEMA) == {"answer": "cold:q0"}  # first use: cold, teaches the key
    assert llm.call_json(SYS, "q1", SCHEMA) == {"answer": "cold:q1"}  # prewarm starts here
    assert _wait_for(lambda: llm._daemon().warm_keys()), "dead fake never registered"

    with caplog.at_level(logging.WARNING, logger="core.llm"):
        assert llm.call_json(SYS, "q2", SCHEMA) == {"answer": "cold:q2"}  # daemon call fails -> cold answer
    assert any("daemon call failed" in r.message for r in caplog.records)  # surfaced, not swallowed

    assert llm.call_json(SYS, "q3", SCHEMA) == {"answer": "cold:q3"}
    assert len(spawns) == 1  # the single q1 prewarm: after its death, cooldown blocks all further spawns
    assert time.monotonic() - llm._daemon()._cooldown[key] < 5  # the failed key is backing off


def test_daemon_timeout_falls_back_to_cold_quickly(monkeypatch):
    monkeypatch.setenv("MYCELIUM_MODEL_DAEMON", "1")

    def fake_popen(args, **kw):
        return FakeDaemonCLI(args, startup_s=0.0, hang=True)  # accepts the prompt, never answers

    monkeypatch.setattr(llm.subprocess, "Popen", fake_popen)
    cold_calls = _cold_ok(monkeypatch)

    assert llm.call_json(SYS, "q0", SCHEMA) == {"answer": "cold:q0"}
    assert llm.call_json(SYS, "q1", SCHEMA) == {"answer": "cold:q1"}  # prewarm starts; hangs forever
    assert _wait_for(lambda: llm._daemon().warm_keys()), "warm process never registered"

    t0 = time.monotonic()
    answer = llm.call_json(SYS, "q2", SCHEMA, timeout_s=0.3)  # hung daemon must not eat the cold budget
    wall = time.monotonic() - t0

    assert answer == {"answer": "cold:q2"}  # caller got a real answer from the cold path
    assert wall < 2.0, f"fallback took {wall:.2f}s"  # bounded by the daemon timeout, not by a hung process
    assert cold_calls == ["q0", "q1", "q2"]


def test_missing_cli_degrades_to_cold_without_churn(monkeypatch):
    monkeypatch.setenv("MYCELIUM_MODEL_DAEMON", "1")
    attempts: list[int] = []

    def fake_popen(args, **kw):
        attempts.append(1)
        raise FileNotFoundError("claude not on PATH")  # daemon cannot even spawn

    monkeypatch.setattr(llm.subprocess, "Popen", fake_popen)
    cold_calls = _cold_ok(monkeypatch)

    assert llm.call_json(SYS, "q0", SCHEMA) == {"answer": "cold:q0"}
    assert llm.call_json(SYS, "q1", SCHEMA) == {"answer": "cold:q1"}  # spawn attempt fails, cooldown starts
    assert llm.call_json(SYS, "q2", SCHEMA) == {"answer": "cold:q2"}
    assert llm.call_json(SYS, "q3", SCHEMA) == {"answer": "cold:q3"}
    assert len(attempts) == 1  # exactly one spawn attempt, then quiet backoff — no per-call churn
    assert len(cold_calls) == 4  # every question answered by the cold path
