"""The single model gateway: shells out to `claude --print`, validates JSON against a schema, retries, rate-limits per bucket, logs usage.

Transport-level speed features: `call_json_concurrent` runs independent calls on the wall clock, and the
opt-in ModelDaemon (MYCELIUM_MODEL_DAEMON=1) serves calls from pre-warmed CLI processes so they stop
waiting on per-call startup. Every daemon failure falls back to the cold `claude --print` path with the
same semantics.

Reliability features: token accounting (`call_json`/`call_text` fold the CLI envelope's usage into a
caller-supplied dict, summed across attempts), an in-process session response cache (repeat questions
in a session return the cached answer marked `cached: true` with no model call), and per-bucket timeout
tiers (`bucket_timeout`: follow-up questions get a short timeout, reflection a long one, both
configurable via env).
"""
from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import logging
import os
import queue
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
_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL)
log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class DaemonUnavailable(LLMError):
    """No warm process served the call (first use, pool cap, cooldown) — expected; the cold path proceeds."""


class DaemonDied(LLMError):
    """A warm process died mid-call — often a CLI build that cannot serve stream-json; cool the key down."""


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
        proc = subprocess.run(args, check=False, input=user, capture_output=True, text=True, timeout=timeout_s, env=env or os.environ)
        ok = proc.returncode == 0
        usage = _usage_from_proc(proc)
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


# ---------------------------------------------------------------------------------------------------- reliability primitives (features 23, 25, 26)
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        log.warning("bad %s, using %s", name, default)
        return default


def _usage_from_proc(proc: subprocess.CompletedProcess) -> dict | None:
    """The usage object from a CLI JSON envelope, or None when the envelope carries none."""
    try:
        usage = json.loads(proc.stdout).get("usage")
    except (json.JSONDecodeError, AttributeError):
        return None
    return usage if isinstance(usage, dict) else None


def _merge_usage(out: dict | None, attempt_usage: dict | None) -> None:
    """Fold one attempt's CLI usage into the caller's accounting dict; tokens sum across attempts."""
    if out is None:
        return
    if isinstance(attempt_usage, dict):
        for k, v in attempt_usage.items():
            if isinstance(v, (int, float)):
                out[k] = out.get(k, 0) + v
    out["attempts"] = out.get("attempts", 0) + 1


BUCKET_TIMEOUTS = {"follow_up": 20.0, "reflect": 120.0, "voice": 60.0, "growth": 300.0}


def bucket_timeout(bucket: str) -> float:
    """Default timeout in seconds for a bucket tier: follow-up questions get a short timeout, reflection a
    long one. MYCELIUM_TIMEOUT_<BUCKET>_S overrides any tier; unknown buckets fall back to 60s."""
    return _env_float(f"MYCELIUM_TIMEOUT_{(bucket or '').upper()}_S", BUCKET_TIMEOUTS.get(bucket, 60.0))


SESSION_CACHE_MAX = 256  # bounded: one entry per distinct question text per session
_session_cache: dict[tuple[str, str, str, str], tuple[float, dict]] = {}
_session_cache_lock = threading.Lock()


def session_cache_enabled() -> bool:
    """On by default for callers that pass a session; MYCELIUM_SESSION_CACHE=0 turns it off."""
    raw = os.environ.get("MYCELIUM_SESSION_CACHE")
    return True if raw is None else raw.strip().lower() not in ("0", "off", "false", "no")


def _cache_key(session: str, system: str, schema: dict | None, user: str) -> tuple[str, str, str, str]:
    s = json.dumps(schema, sort_keys=True) if schema else "-"
    return (str(session), system, s, user)


def session_cache_get(session: str | None, system: str, schema: dict | None, user: str) -> dict | None:
    """The cached answer for this exact call within the session, marked cached: true; None on any miss."""
    if not session or not session_cache_enabled():
        return None
    key = _cache_key(session, system, schema, user)
    with _session_cache_lock:
        row = _session_cache.get(key)
    if row is None:
        return None
    ts, val = row
    if time.monotonic() - ts >= _env_float("MYCELIUM_SESSION_CACHE_TTL_S", 900.0):
        with _session_cache_lock:
            _session_cache.pop(key, None)
        return None
    return dict(val, cached=True)


def session_cache_put(session: str | None, system: str, schema: dict | None, user: str, result: dict) -> None:
    if not session or not session_cache_enabled():
        return
    with _session_cache_lock:
        if len(_session_cache) >= SESSION_CACHE_MAX:
            _session_cache.pop(min(_session_cache, key=lambda k: _session_cache[k][0]))
        _session_cache[_cache_key(session, system, schema, user)] = (time.monotonic(), dict(result))


def reset_session_cache() -> None:
    """Drop every cached answer — ops/test hook."""
    with _session_cache_lock:
        _session_cache.clear()


def _schema_retry_loop(run_once, user: str, schema: dict, retries: int, usage: dict | None = None) -> dict:
    """Shared validation loop: run → check → parse → validate; on a schema miss, re-prompt with the error."""
    prompt = user
    last: Exception | None = None
    for _ in range(retries + 1):
        proc = run_once(prompt)
        _merge_usage(usage, _usage_from_proc(proc))  # count every attempt, even ones that then fail validation
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


def call_json(system: str, user: str, schema: dict, *, timeout_s: float | None = None, bucket: str = "voice",
              retries: int = 2, env: dict | None = None, usage: dict | None = None,
              session: str | None = None) -> dict:
    """One schema-validated model call.

    usage: optional accounting dict — the CLI envelope's usage fields (summed across every attempt,
    including failed ones) plus an attempts counter, so callers can land tokens in the answer and the ledger.
    session: opt-in cache scope — an identical (system, user, schema) call in the same session returns the
    cached answer marked cached: true without any model call (feature 25).
    timeout_s: explicit value wins; otherwise the bucket's timeout tier applies (feature 26).
    """
    t_s = timeout_s if timeout_s is not None else bucket_timeout(bucket)
    hit = session_cache_get(session, system, schema, user)
    if hit is not None:
        return hit
    result = None
    if env is None and daemon_enabled():
        try:
            result = _schema_retry_loop(
                lambda prompt: _daemon().run(system, schema, prompt, timeout_s=t_s, bucket=bucket),
                user, schema, retries, usage)
        except DaemonUnavailable as e:
            log.debug("daemon not serving this call, using cold CLI: %s", e)
        except LLMError as e:
            log.warning("daemon call failed, using cold CLI: %s", e)

    if result is None:
        def run_once(prompt: str):
            return _cold_run_schema(system, schema, prompt, timeout_s=t_s, bucket=bucket, env=env)

        result = _schema_retry_loop(run_once, user, schema, retries, usage)
    session_cache_put(session, system, schema, user, result)
    return result


def call_text(system: str, user: str, *, timeout_s: float | None = None, bucket: str = "voice",
              env: dict | None = None, usage: dict | None = None) -> str:
    t_s = timeout_s if timeout_s is not None else bucket_timeout(bucket)
    if env is None and daemon_enabled():
        try:
            proc = _daemon().run(system, None, user, timeout_s=t_s, bucket=bucket)
            _check(proc)
            _merge_usage(usage, _usage_from_proc(proc))
            return str(json.loads(proc.stdout).get("result", "")).strip()
        except DaemonUnavailable as e:
            log.debug("daemon not serving this call, using cold CLI: %s", e)
        except (LLMError, json.JSONDecodeError, AttributeError) as e:
            log.warning("daemon call failed, using cold CLI: %s", e)
    proc = _run(cli_args(system, None), user, t_s, bucket, env)
    _check(proc)
    _merge_usage(usage, _usage_from_proc(proc))
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
    timeout_s: float | None = None  # None defers to the bucket's timeout tier
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


# ---------------------------------------------------------------------------------------------------- model daemon (feature 22)
DAEMON_ENV = "MYCELIUM_MODEL_DAEMON"


def daemon_enabled() -> bool:
    """Warm model path is opt-in: MYCELIUM_MODEL_DAEMON=1 routes host CLI calls through pre-warmed processes."""
    return os.environ.get(DAEMON_ENV, "").strip().lower() in ("1", "on", "true", "yes")


@dataclass
class _DaemonProc:
    proc: subprocess.Popen
    events: queue.Queue
    spawned_at: float


class ModelDaemon:
    """Warm pool of claude processes in stream-json mode, keyed by (system prompt, schema).

    Latency without behavior drift: each warm process serves exactly ONE call — a fresh conversation,
    so answers are identical to the cold path — and is replaced by a background spawn, so the next call
    on the same key never waits on CLI startup. One-shot keys (per-question synthesis prompts embed the
    retrieved context, so they never repeat) are not pre-warmed; repeated keys (readings, reflection)
    go warm from their third call on. Every daemon failure raises LLMError; call_json/call_text fall
    back to the cold CLI path, so the daemon can only ever add speed, never break an answer.
    """

    def __init__(self, max_procs: int = 2, idle_ttl_s: float = 600.0, cooldown_s: float = 30.0):
        self.max_procs = max_procs
        self.idle_ttl_s = idle_ttl_s
        self.cooldown_s = cooldown_s
        self._warm: dict[str, _DaemonProc] = {}
        self._prewarming: set[str] = set()
        self._cooldown: dict[str, float] = {}
        self._key_hits: dict[str, int] = {}
        self._live = 0
        self._lock = threading.Lock()

    @staticmethod
    def key(system: str, schema: dict | None) -> str:
        """Stable short key for a (system, schema) pair — hashed so corpus text never reaches the logs."""
        s = hashlib.sha1(system.encode()).hexdigest()[:12]
        p = hashlib.sha1((json.dumps(schema, sort_keys=True) if schema else "-").encode()).hexdigest()[:12]
        return f"{s}:{p}"

    @staticmethod
    def daemon_args(system: str, schema: dict | None) -> list[str]:
        # The cold-path invocation plus stream-json I/O, so a spawned process can idle-warm on an open
        # stdin and take one user message. If the installed CLI rejects any flag, the process dies on
        # first use, the key cools down, and every call takes the cold path — demo-safe by construction.
        args = ["claude", "--print", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--max-turns", "3", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                "--system-prompt", system]
        return args + (["--json-schema", json.dumps(schema)] if schema else [])

    def run(self, system: str, schema: dict | None, user: str, *, timeout_s: float,
            bucket: str) -> subprocess.CompletedProcess:
        """One model call on a warm process. Raises DaemonUnavailable when none is warm (first use —
        expected) and LLMError when a warm call fails; the caller falls back to the cold CLI path."""
        key = self.key(system, schema)
        warm = self._take_warm(key)
        if warm is None:
            self._note_miss_and_prewarm(key, system, schema)
            raise DaemonUnavailable(f"no warm process for key={key}")
        t0 = time.monotonic()
        try:
            ev = self._exchange(warm, user, timeout_s)
        except DaemonDied as e:
            self._kill(warm)
            self._cool(key, e)
            raise
        except LLMError:
            self._kill(warm)
            raise
        self._kill(warm)  # one call per process: no conversation carries between questions
        usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else None
        _log(bucket, int((time.monotonic() - t0) * 1000), usage, True)
        self._maybe_prewarm(key, system, schema)  # background replacement for the next call on this key
        return subprocess.CompletedProcess(args=self.daemon_args(system, schema), returncode=0,
                                           stdout=json.dumps(ev), stderr="")

    def warm_keys(self) -> list[str]:
        with self._lock:
            return list(self._warm)

    def pending_spawns(self) -> int:
        with self._lock:
            return len(self._prewarming)

    def stop_all(self) -> None:
        with self._lock:
            procs = list(self._warm.values())
            self._warm.clear()
        for w in procs:
            self._kill(w)

    # ------------------------------------------------------------------ internals
    def _exchange(self, warm: _DaemonProc, user: str, timeout_s: float) -> dict:
        """Write one stream-json user message, read events until the result; typed LLMError on every failure."""
        deadline = time.monotonic() + timeout_s
        try:
            msg = json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": user}]}})
            warm.proc.stdin.write(msg + "\n")
            warm.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise DaemonDied(f"daemon stdin write failed: {e}") from e
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMError(f"daemon result not ready after {timeout_s}s")
            try:
                ev = warm.events.get(timeout=remaining)
            except queue.Empty as e:
                raise LLMError(f"daemon timed out after {timeout_s}s") from e
            if ev is None:
                raise DaemonDied("daemon process exited before returning a result")
            if not isinstance(ev, dict) or ev.get("type") != "result":
                continue
            if ev.get("is_error"):
                raise LLMError(f"daemon error: {str(ev.get('result'))[:300]}")
            return ev

    def _take_warm(self, key: str) -> _DaemonProc | None:
        with self._lock:
            warm = self._warm.pop(key, None)
        if warm is None:
            return None
        if time.monotonic() - warm.spawned_at > self.idle_ttl_s:
            self._kill(warm)
            return None
        return warm

    def _note_miss_and_prewarm(self, key: str, system: str, schema: dict | None) -> None:
        with self._lock:
            self._key_hits[key] = self._key_hits.get(key, 0) + 1
            hits = self._key_hits[key]
            if len(self._key_hits) > 128:  # bounded: one-shot synthesis keys never stop arriving
                self._key_hits.pop(next(iter(self._key_hits)))
        if hits >= 2:  # a key seen once is one-shot; only repeat keys earn a warm process
            self._maybe_prewarm(key, system, schema)

    def _maybe_prewarm(self, key: str, system: str, schema: dict | None) -> None:
        with self._lock:
            if key in self._warm or key in self._prewarming:
                return
            if time.monotonic() - self._cooldown.get(key, -1e9) < self.cooldown_s:
                return
            if self._live >= self.max_procs:
                return
            self._prewarming.add(key)
            self._live += 1  # reserved; _spawn_reserved releases on failure
        threading.Thread(target=self._spawn_reserved, args=(key, system, schema), daemon=True).start()

    def _spawn_reserved(self, key: str, system: str, schema: dict | None) -> None:
        ok = False
        try:
            ok = self._spawn(key, system, schema)
        finally:
            with self._lock:
                self._prewarming.discard(key)
                if not ok:
                    self._live -= 1
        if not ok:
            self._cool(key, LLMError("daemon spawn failed"))

    def _spawn(self, key: str, system: str, schema: dict | None) -> bool:
        try:
            proc = subprocess.Popen(self.daemon_args(system, schema), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, bufsize=1, start_new_session=True)
        except (OSError, ValueError) as e:
            log.warning("daemon spawn failed (key=%s): %s", key, e)
            return False
        events: queue.Queue = queue.Queue()
        threading.Thread(target=self._reader, args=(proc, events), daemon=True).start()
        with self._lock:
            self._warm[key] = _DaemonProc(proc=proc, events=events, spawned_at=time.monotonic())
        return True

    @staticmethod
    def _reader(proc: subprocess.Popen, events: queue.Queue) -> None:
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            events.put(None)

    def _kill(self, warm: _DaemonProc) -> None:
        with self._lock:
            self._live -= 1
        try:
            warm.proc.kill()
        except OSError:
            pass  # daemon already exited: kill is idempotent here, nothing to surface

    def _cool(self, key: str, err: Exception) -> None:
        with self._lock:
            if len(self._cooldown) > 64:
                self._cooldown.pop(next(iter(self._cooldown)))
            self._cooldown[key] = time.monotonic()
        log.warning("daemon key=%s cooling down %.0fs after: %s", key, self.cooldown_s, err)


_daemon_instance: ModelDaemon | None = None
_daemon_lock = threading.Lock()


def _daemon() -> ModelDaemon:
    global _daemon_instance
    with _daemon_lock:
        if _daemon_instance is None:
            _daemon_instance = ModelDaemon(
                max_procs=_env_int("MYCELIUM_DAEMON_MAX_PROCS", 2),
                idle_ttl_s=_env_float("MYCELIUM_DAEMON_IDLE_TTL_S", 600.0),
                cooldown_s=_env_float("MYCELIUM_DAEMON_COOLDOWN_S", 30.0))
            atexit.register(_daemon_instance.stop_all)
        return _daemon_instance


def reset_daemon() -> None:
    """Stop and forget the pool — ops/test hook; the next enabled call builds a fresh one."""
    global _daemon_instance
    with _daemon_lock:
        if _daemon_instance is not None:
            _daemon_instance.stop_all()
            _daemon_instance = None
