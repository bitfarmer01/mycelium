"""The single model gateway: shells out to `claude --print`, validates JSON against a schema, retries, rate-limits per bucket, logs usage."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import jsonschema

USAGE_LOG = Path(__file__).resolve().parents[1] / "data" / "usage.jsonl"
BUCKET_LIMITS = {"growth": (10, 60.0)}  # name -> (max calls, per seconds); unlisted buckets are unlimited
_bucket_hist: dict[str, deque] = {}
_lock = threading.Lock()
_FENCE = re.compile(r"```json\s*(.*?)```", re.S)
log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


def cli_args(system: str, schema: dict | None) -> list[str]:
    args = ["claude", "--print", "--output-format", "json", "--max-turns", "3", "--tools", "",
            "--strict-mcp-config", "--no-session-persistence", "--system-prompt", system]
    return args + (["--json-schema", json.dumps(schema)] if schema else [])


def bedrock_env() -> dict:
    """Env for Claude Code inside Daytona sandboxes. Never applied to host calls by default."""
    env = {k: v for k, v in os.environ.items() if k.startswith("AWS_")}
    env.update({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2",
                "ANTHROPIC_MODEL": "us.anthropic.claude-opus-4-5-20251101-v1:0"})
    return env


def _parse_text_json(text: str) -> dict:
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            return val
    except (json.JSONDecodeError, TypeError):
        pass
    m = _FENCE.search(text or "")
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise LLMError(f"bad fenced json: {e}") from e
    raise LLMError("no JSON object in model result")


def parse_result(stdout: str) -> dict:
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise LLMError(f"CLI stdout is not JSON: {e}") from e
    if isinstance(env.get("structured_output"), dict):
        return env["structured_output"]
    return _parse_text_json(env.get("result", ""))


def _throttle(bucket: str) -> None:
    lim = BUCKET_LIMITS.get(bucket)
    if not lim:
        return
    n, per = lim
    while True:
        with _lock:
            hist = _bucket_hist.setdefault(bucket, deque())
            now = time.monotonic()
            while hist and now - hist[0] >= per:
                hist.popleft()
            if len(hist) < n:
                hist.append(time.monotonic())
                return
            wait = max(0.0, per - (now - hist[0]))
        # sleep outside the lock: a full bucket must never stall calls in other buckets
        time.sleep(wait)


def _log(bucket: str, ms: int, usage, ok: bool) -> None:
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.time(), "bucket": bucket, "ms": ms, "usage": usage if isinstance(usage, dict) else None, "ok": ok}
        with USAGE_LOG.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def _run(args: list[str], user: str, timeout_s: float, bucket: str, env: dict | None) -> subprocess.CompletedProcess:
    _throttle(bucket)
    t0 = time.monotonic()
    usage, ok = None, False
    try:
        proc = subprocess.run(args, input=user, capture_output=True, text=True, timeout=timeout_s, env=env or os.environ)
        ok = proc.returncode == 0
        try:
            usage = json.loads(proc.stdout).get("usage")
        except (json.JSONDecodeError, AttributeError):
            pass
        return proc
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"claude timed out after {timeout_s}s") from e
    except OSError as e:
        raise LLMError(f"claude failed to start: {e}") from e
    finally:
        _log(bucket, int((time.monotonic() - t0) * 1000), usage, ok)


def _check(proc: subprocess.CompletedProcess) -> None:
    if proc.returncode != 0:
        raise LLMError(f"claude exit {proc.returncode}: {(proc.stderr or proc.stdout or '')[:300]}")
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return
    if isinstance(env, dict) and env.get("is_error"):
        raise LLMError(f"claude error: {str(env.get('result'))[:300]}")


def _schema_retry_loop(run_once, user: str, schema: dict, retries: int) -> dict:
    """Shared validation loop: run → check → parse → validate; on a schema miss, re-prompt with the error."""
    prompt = user
    last: Exception | None = None
    for _ in range(retries + 1):
        proc = run_once(prompt)
        _check(proc)
        try:
            out = parse_result(proc.stdout)
            jsonschema.validate(out, schema)
            return out
        except (jsonschema.ValidationError, LLMError) as e:
            last = e
            msg = e.message if isinstance(e, jsonschema.ValidationError) else str(e)
            prompt = (f"{user}\n\nYour previous response did not conform to the required JSON schema. "
                      f"Error: {msg}\nReturn only a corrected JSON object.")
    raise LLMError(f"schema validation failed after {retries + 1} attempts: {last}")


def _cold_run_schema(system: str, schema: dict, user: str, *, timeout_s: float, bucket: str, env: dict | None):
    """One cold CLI attempt; older CLIs that reject --json-schema get one immediate retry with the schema in prose."""
    proc = _run(cli_args(system, schema), user, timeout_s, bucket, env)
    if proc.returncode != 0 and "unknown option" in (proc.stderr + proc.stdout).lower():
        sys_p = f"{system}\n\nRespond with only a JSON object matching this JSON Schema:\n{json.dumps(schema)}"
        return _run(cli_args(sys_p, None), user, timeout_s, bucket, env)
    return proc


def call_json(system: str, user: str, schema: dict, *, timeout_s: float = 60, bucket: str = "voice",
              retries: int = 2, env: dict | None = None) -> dict:
    def run_once(prompt: str):
        return _cold_run_schema(system, schema, prompt, timeout_s=timeout_s, bucket=bucket, env=env)

    return _schema_retry_loop(run_once, user, schema, retries)


def call_text(system: str, user: str, *, timeout_s: float = 60, bucket: str = "voice", env: dict | None = None) -> str:
    proc = _run(cli_args(system, None), user, timeout_s, bucket, env)
    _check(proc)
    try:
        return str(json.loads(proc.stdout).get("result", "")).strip()
    except (json.JSONDecodeError, AttributeError) as e:
        raise LLMError(f"CLI stdout is not JSON: {e}") from e


# ---------------------------------------------------------------------------------------------------- concurrent batch (feature 21)
@dataclass(frozen=True)
class JsonJob:
    """One independent model call for call_json_concurrent."""

    system: str
    user: str
    schema: dict
    timeout_s: float = 60.0
    bucket: str = "voice"


_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        log.warning("bad %s, using %s", name, default)
        return default


def _shared_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=_env_int("MYCELIUM_MAX_MODEL_CALLS", 4),
                                           thread_name_prefix="llm-call")
        return _executor


async def call_json_concurrent(jobs: Sequence[JsonJob], *, return_exceptions: bool = False) -> list:
    """Run independent model calls concurrently; results in input order, full call_json semantics per job.

    Overlaps readings/synthesis-class calls on the wall clock — total time approaches the slowest call,
    not the sum. Each job keeps schema validation, retries, bucket throttling and usage logging. With
    return_exceptions=True, failed jobs surface as exception objects in their slot instead of failing
    the whole batch (one bad exam pack must not kill a scored set).
    """
    loop = asyncio.get_running_loop()
    futs = [loop.run_in_executor(_shared_executor(), partial(call_json, j.system, j.user, j.schema,
                                                             timeout_s=j.timeout_s, bucket=j.bucket))
            for j in jobs]
    return list(await asyncio.gather(*futs, return_exceptions=return_exceptions))
