"""Feature 21: independent model calls overlap on the wall clock, with per-call semantics intact.

No real CLI: subprocess.run is monkeypatched with a thread-safe fake whose answer is derived from the
prompt, so overlap (total < sum of parts) and result equivalence against sequential execution are both
observable without network or model access.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time

import pytest

from core import llm

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE_LOG", tmp_path / "usage.jsonl")
    monkeypatch.delenv("MYCELIUM_MODEL_DAEMON", raising=False)  # cold-path determinism
    llm._bucket_hist.clear()
    yield
    llm._bucket_hist.clear()


def _proc(stdout: str, rc: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=["claude"], returncode=rc, stdout=stdout, stderr=stderr)


def _install_slow_transport(monkeypatch, think_s: float):
    """Pure fake CLI: sleep per call, answer derived from the prompt. Stateless across threads."""
    calls: list[str] = []
    lock = threading.Lock()

    def fake_run(args, input=None, **kw):
        with lock:
            calls.append(input)
        time.sleep(think_s)
        return _proc(json.dumps({"result": "", "structured_output": {"answer": f"ans:{input}"}}))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    return calls


async def test_four_independent_calls_overlap_on_the_wall_clock(monkeypatch):
    _install_slow_transport(monkeypatch, think_s=0.4)
    jobs = [llm.JsonJob(system=f"s{i}", user=f"q{i}", schema=SCHEMA) for i in range(4)]

    t0 = time.monotonic()
    out = await llm.call_json_concurrent(jobs)
    wall = time.monotonic() - t0

    assert [r["answer"] for r in out] == [f"ans:q{i}" for i in range(4)]  # input order preserved
    assert wall < 4 * 0.4 * 0.75  # total < sum of parts: the four sleeps overlapped


async def test_concurrent_results_match_sequential_execution(monkeypatch):
    _install_slow_transport(monkeypatch, think_s=0.01)
    jobs = [llm.JsonJob(system="s", user=f"q{i}", schema=SCHEMA) for i in range(5)]

    concurrent = await llm.call_json_concurrent(jobs)
    sequential = [llm.call_json(j.system, j.user, j.schema) for j in jobs]

    assert concurrent == sequential  # same answers the cold path produces: quality unchanged


async def test_validation_still_applies_per_job(monkeypatch):
    def fake_run(args, input=None, **kw):
        payload = {"answer": 1} if input.startswith("bad") else {"answer": f"ans:{input}"}  # "bad" violates the schema
        return _proc(json.dumps({"result": "", "structured_output": payload}))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    jobs = [llm.JsonJob("s", "bad", SCHEMA), llm.JsonJob("s", "good", SCHEMA)]

    with pytest.raises(llm.LLMError):  # strict by default: a schema-invalid answer fails the batch
        await llm.call_json_concurrent(jobs)
    out = await llm.call_json_concurrent(jobs, return_exceptions=True)
    assert isinstance(out[0], llm.LLMError)
    assert out[1] == {"answer": "ans:good"}  # valid siblings still deliver


async def test_throttle_sleep_releases_the_lock(monkeypatch):
    """Regression: a full bucket sleeping out its window must not stall other buckets (or other threads)."""
    monkeypatch.setattr(llm, "BUCKET_LIMITS", {"a": (1, 1.0)})
    llm._throttle("a")  # bucket a is now full for 1s
    results: list[float] = []

    def blocked():
        llm._throttle("a")  # must sleep ~1s for the window to roll
        results.append(time.monotonic())

    th = threading.Thread(target=blocked)
    t0 = time.monotonic()
    th.start()
    await asyncio.sleep(0.05)  # non-blocking: the event loop must stay live while the thread throttles
    llm._throttle("b")  # unthrottled bucket: must not wait on a's lock-held sleep
    other_wait = time.monotonic() - t0
    th.join(timeout=3)

    assert results, "throttled thread never completed"
    assert other_wait < 0.3, f"other bucket waited {other_wait:.2f}s behind a sleeping throttle"
    assert results[0] - t0 >= 0.5  # bucket a still waited out its window


async def test_throttled_bucket_does_not_block_concurrent_other_bucket(monkeypatch):
    _install_slow_transport(monkeypatch, think_s=0.0)
    monkeypatch.setattr(llm, "BUCKET_LIMITS", {"growth": (1, 0.4)})
    await llm.call_json_concurrent([llm.JsonJob("s", "q", SCHEMA, bucket="growth")])  # growth window opens

    throttled = asyncio.create_task(
        llm.call_json_concurrent([llm.JsonJob("s", "q2", SCHEMA, bucket="growth")]))
    await asyncio.sleep(0.05)  # throttled call is now sleeping out its window
    t0 = time.monotonic()
    out = await llm.call_json_concurrent([llm.JsonJob("s", "q3", SCHEMA, bucket="voice")])
    voice_wall = time.monotonic() - t0

    assert out == [{"answer": "ans:q3"}]
    assert voice_wall < 0.3, f"voice call waited {voice_wall:.2f}s behind the growth throttle"
    assert await throttled == [{"answer": "ans:q2"}]  # drained in-test: no call thread outlives the test


async def test_concurrent_batch_respects_the_bucket_limit(monkeypatch):
    """The shared throttle still caps a concurrent burst: 3 calls at limit 1 take >= 2 windows."""
    _install_slow_transport(monkeypatch, think_s=0.0)
    monkeypatch.setattr(llm, "BUCKET_LIMITS", {"growth": (1, 0.3)})
    jobs = [llm.JsonJob("s", f"q{i}", SCHEMA, bucket="growth") for i in range(3)]

    t0 = time.monotonic()
    out = await llm.call_json_concurrent(jobs)
    wall = time.monotonic() - t0

    assert [r["answer"] for r in out] == [f"ans:q{i}" for i in range(3)]
    assert wall >= 0.6  # limit enforced under concurrency, not bypassed
