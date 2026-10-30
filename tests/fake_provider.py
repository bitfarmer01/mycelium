"""Test double for the exam sandbox pool: the SandboxProvider shape, zero external calls.

The live provider (core.exam.DaytonaPoolProvider) is unreachable in CI — Bedrock auth
for sandbox claude is expired (spec open question) and Daytona needs a real key — so
pool tests run against this double, which implements the identical interface and
records every call for count assertions. exec() echoes one EXAM row per pack the
runner uploaded for that sandbox, with a canned per-pack result (default: None =
unanswerable, which scores 0).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


class FakeSandboxProvider:
    name = "fake"

    def __init__(self, result: dict | None = None, exec_delay: float = 0.0):
        self.create_calls = 0
        self.deleted: list[str] = []
        self.uploaded: list[tuple[str, str]] = []
        self.execs: list[str] = []
        self.closed = False
        self.exec_delay = exec_delay
        self._result = result
        self._packs: dict[str, list[dict]] = {}
        self._next = 0

    async def create(self, label: str):
        await asyncio.sleep(0)  # yield so concurrent runs interleave at acquisition time
        self.create_calls += 1
        sandbox = SimpleNamespace(id=f"fake-{self._next}", label=label)
        self._next += 1
        return sandbox

    async def upload(self, sandbox, path: str, data: bytes) -> None:
        self.uploaded.append((str(sandbox.id), path))
        if path == "/tmp/packs.json":
            self._packs[str(sandbox.id)] = json.loads(data)

    async def exec(self, sandbox, command: str, timeout: float) -> str:
        self.execs.append(str(sandbox.id))
        if self.exec_delay:
            await asyncio.sleep(self.exec_delay)
        rows = [{"question_id": p["question_id"], "result": self._result, "ms": 1, "err": None}
                for p in self._packs.get(str(sandbox.id), [])]
        return "\n".join("EXAM " + json.dumps(r) for r in rows)

    async def delete(self, sandbox) -> None:
        self.deleted.append(str(sandbox.id))

    def env_bytes(self) -> bytes:
        return b"export FAKE_POOL=1\n"

    def exam_command(self) -> str:
        return "python3 /tmp/exam.py"

    async def close(self) -> None:
        self.closed = True
