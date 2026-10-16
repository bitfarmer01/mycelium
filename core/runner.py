"""Exam runner: run a slot over evidence packs in a Daytona sandbox (Claude Code via Bedrock) or locally on the host.

The slot is rendered on the host per pack (render_prompt already appends the CONTRACT) and shipped inside packs.json,
so the sandbox needs no core imports. core.growth / core.llm / core.retrieve are imported lazily (growth imports runner).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Protocol, TypedDict

from core.contracts import SYNTHESIS_SCHEMA

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PACKS_PATH = ROOT / "data" / "heldout_packs.json"
MODEL_ID = "us.anthropic.claude-opus-4-5-20251101-v1:0"
BEDROCK_REGION = "us-west-2"
SNAPSHOT = "daytona-small"  # 3 x small fits the 10 GiB org pool (spike DAY-9x)
EXEC_TIMEOUT = 900
LOCAL_CONCURRENCY = 4
SANDBOX_CONCURRENCY = 4
PER_CALL_TIMEOUT = 300
SYS_PLACEHOLDER = "__MYC_SYSTEM__"
PATHX = "export PATH=$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:$PATH"


class RunResult(TypedDict):
    runner: str
    sandbox_id: str
    answers: list[dict]  # [{"question_id", "result": dict|None, "ms"}]
    ms: int


class Runner(Protocol):
    async def run(self, slot: str, packs: list[dict], label: str) -> RunResult: ...


# ---------------------------------------------------------------------------------------------------- helpers
def _cli_args(system: str) -> list[str]:
    try:
        from core import llm
        return llm.cli_args(system, SYNTHESIS_SCHEMA)
    except (ImportError, AttributeError):
        args = ["claude", "--print", "--output-format", "json", "--max-turns", "3", "--tools", "",
                "--strict-mcp-config", "--no-session-persistence", "--system-prompt", system]
        return args + ["--json-schema", json.dumps(SYNTHESIS_SCHEMA)]


def _bedrock_env() -> dict:
    try:
        from core import llm
        env = dict(llm.bedrock_env())
    except (ImportError, AttributeError):
        env = {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": BEDROCK_REGION, "ANTHROPIC_MODEL": MODEL_ID}
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def _parse(stdout: str) -> dict | None:
    try:
        from core import llm
        return llm.parse_result(stdout)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return None
    try:
        env = json.loads(stdout)
        if isinstance(env.get("structured_output"), dict):
            return env["structured_output"]
        val = json.loads(env.get("result", ""))
        return val if isinstance(val, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _render(slot: str, packs: list[dict]) -> list[dict]:
    """Host-side rendering: one {"question_id", "system", "user"} per pack (render_prompt already has the CONTRACT)."""
    from core import growth
    return [{"question_id": p["question_id"], "system": growth.render_prompt(slot, p["context"], p["text"]),
             "user": p["text"]} for p in packs]


def exam_script() -> str:
    """Self-contained Python run inside the sandbox: reads /tmp/packs.json, prints one EXAM JSON line per pack."""
    base = _cli_args(SYS_PLACEHOLDER)
    return f'''import json, subprocess, time
from concurrent.futures import ThreadPoolExecutor

BASE = {base!r}
PH = {SYS_PLACEHOLDER!r}


def parse(out):
    try:
        env = json.loads(out)
    except Exception:
        return None
    if isinstance(env, dict) and env.get("is_error"):
        return None
    so = env.get("structured_output") if isinstance(env, dict) else None
    if isinstance(so, dict):
        return so
    text = env.get("result", "") if isinstance(env, dict) else ""
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else None
    except Exception:
        pass
    if "```json" in text:
        try:
            return json.loads(text.split("```json", 1)[1].split("```", 1)[0])
        except Exception:
            return None
    return None


def one(pack):
    args = [pack["system"] if a == PH else a for a in BASE]
    t0 = time.time()
    result, err = None, None
    try:
        p = subprocess.run(args, input=pack["user"], capture_output=True, text=True, timeout={PER_CALL_TIMEOUT})
        result = parse(p.stdout)
        if result is None:
            err = "rc=%s %s" % (p.returncode, (p.stderr or p.stdout or "")[-200:])
    except Exception as e:
        err = repr(e)[:200]
    return {{"question_id": pack["question_id"], "result": result, "ms": int((time.time() - t0) * 1000), "err": err}}


packs = json.load(open("/tmp/packs.json"))
with ThreadPoolExecutor({SANDBOX_CONCURRENCY}) as pool:
    for row in pool.map(one, packs):
        print("EXAM " + json.dumps(row), flush=True)
'''


def _parse_exam_output(text: str, packs: list[dict]) -> list[dict]:
    got = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("EXAM "):
            continue
        try:
            row = json.loads(line[5:])
        except json.JSONDecodeError:
            continue
        if row.get("err"):
            log.warning("exam %s: %s", row.get("question_id"), row["err"])
        got[row.get("question_id")] = {"question_id": row.get("question_id"), "result": row.get("result"),
                                       "ms": int(row.get("ms") or 0)}
    return [got.get(p["question_id"], {"question_id": p["question_id"], "result": None, "ms": 0}) for p in packs]


# ---------------------------------------------------------------------------------------------------- runners
class LocalRunner:
    """subprocess `claude --print` per pack on the host (host's normal claude auth, never Bedrock env)."""

    name = "local"

    def _one(self, item: dict) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_MODEL")}
        t0 = time.monotonic()
        result = None
        try:
            proc = subprocess.run(_cli_args(item["system"]), input=item["user"], capture_output=True, text=True,
                                  timeout=PER_CALL_TIMEOUT, env=env)
            if proc.returncode == 0:
                result = _parse(proc.stdout)
            else:
                log.warning("local exam %s: exit %s", item["question_id"], proc.returncode)
        except Exception as e:  # noqa: BLE001
            log.warning("local exam %s failed: %s", item["question_id"], e)
        return {"question_id": item["question_id"], "result": result, "ms": int((time.monotonic() - t0) * 1000)}

    async def run(self, slot: str, packs: list[dict], label: str) -> RunResult:
        t0 = time.monotonic()
        items = _render(slot, packs)
        sem = asyncio.Semaphore(LOCAL_CONCURRENCY)

        async def go(it):
            async with sem:
                return await asyncio.to_thread(self._one, it)

        answers = list(await asyncio.gather(*(go(it) for it in items)))
        return RunResult(runner="local", sandbox_id=f"local-{label}", answers=answers,
                         ms=int((time.monotonic() - t0) * 1000))


class DaytonaRunner:
    """One ephemeral daytona-small sandbox per run; Claude Code inside authenticates via Bedrock env."""

    name = "daytona"

    def __init__(self, snapshot: str = SNAPSHOT):
        self.snapshot = snapshot

    @staticmethod
    def _env_bytes() -> bytes:
        # built in memory only; never written to local disk, never logged
        return "".join(f"export {k}={shlex.quote(str(v))}\n" for k, v in _bedrock_env().items()).encode()

    async def run(self, slot: str, packs: list[dict], label: str) -> RunResult:
        from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams, DaytonaConfig

        t0 = time.monotonic()
        items = _render(slot, packs)
        params = CreateSandboxFromSnapshotParams(
            snapshot=self.snapshot, auto_stop_interval=0, ephemeral=True,
            name=f"myc-{label}-{uuid.uuid4().hex[:6]}"[:60], labels={"app": "mycelium", "label": label})
        client = AsyncDaytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))
        sandbox = None
        try:
            sandbox = await client.create(params, timeout=240)
            await sandbox.process.exec(
                # snapshot ships claude 2.1.19; upgrade only if it lacks the flags cli_args needs
                f"bash -lc '{PATHX}; claude --help 2>&1 | grep -q -- --json-schema || npm i -g @anthropic-ai/claude-code@latest "
                ">/tmp/inst.log 2>&1 || sudo -n npm i -g @anthropic-ai/claude-code >>/tmp/inst.log 2>&1; "
                "claude --version'", timeout=600)
            await sandbox.fs.upload_file(self._env_bytes(), "/tmp/env")
            await sandbox.fs.upload_file(json.dumps(items).encode(), "/tmp/packs.json")
            await sandbox.fs.upload_file(exam_script().encode(), "/tmp/exam.py")
            resp = await sandbox.process.exec(
                f"bash -lc '{PATHX}; set -a; . /tmp/env; set +a; rm -f /tmp/env; cd /tmp && python3 /tmp/exam.py'",
                timeout=EXEC_TIMEOUT)
            answers = _parse_exam_output(resp.result, packs)
            if not any(a["result"] for a in answers):
                log.warning("daytona run %s: no answers (exit %s)", label, resp.exit_code)
            return RunResult(runner="daytona", sandbox_id=str(sandbox.id), answers=answers,
                             ms=int((time.monotonic() - t0) * 1000))
        finally:
            if sandbox is not None:
                try:
                    await client.delete(sandbox, timeout=120)
                except Exception as e:  # noqa: BLE001
                    log.warning("sandbox delete failed: %s", e)
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------------------------------- selection
def bedrock_health() -> bool:
    """5-token converse on the Bedrock model id with a 10 s timeout; False on any exception."""
    try:
        import boto3
        from botocore.config import Config

        kw = {k.lower(): os.environ[k] for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
              if os.environ.get(k)}
        client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION,
                              config=Config(connect_timeout=10, read_timeout=10, retries={"max_attempts": 1}), **kw)
        client.converse(modelId=MODEL_ID, messages=[{"role": "user", "content": [{"text": "Say OK"}]}],
                        inferenceConfig={"maxTokens": 5})
        return True
    except Exception:  # noqa: BLE001
        return False


def make_runner() -> Runner:
    if os.environ.get("DAYTONA_API_KEY") and bedrock_health():
        return DaytonaRunner()
    return LocalRunner()


async def build_packs() -> list[dict]:
    """For each held-out id: retrieve() -> evidence_pack; writes data/heldout_packs.json."""
    from core import retrieve

    ids = json.loads((ROOT / "data" / "heldout.json").read_text())
    if isinstance(ids, dict):
        ids = ids.get("ids") or ids.get("heldout") or ids.get("questions") or []
    ids = [i["id"] if isinstance(i, dict) else i for i in ids]
    raw = json.loads((ROOT / "data" / "questions.json").read_text())
    if isinstance(raw, dict):
        raw = raw.get("questions", [])
    questions = {q["id"]: q for q in raw}
    packs = []
    for qid in ids:
        text = questions[qid]["text"]
        context, citations, _rows = await retrieve.retrieve(text)
        packs.append(retrieve.evidence_pack(qid, text, context, citations))
    PACKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PACKS_PATH.write_text(json.dumps(packs, indent=2))
    return packs
