"""Shared fixtures: fake_llm (queued call_json results), fake_db (in-memory q recorder), integration marker."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SURREAL_URL"):
        return
    skip = pytest.mark.skip(reason="SURREAL_URL unset")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


class FakeLLM:
    """Queue dicts (or Exceptions) with .push(); each core.llm.call_json pops the next one."""

    def __init__(self):
        self.queue: list = []
        self.calls: list[dict] = []

    def push(self, *items):
        self.queue.extend(items)
        return self

    def __call__(self, system, user, schema=None, **kw):
        self.calls.append({"system": system, "user": user, "schema": schema, **kw})
        if not self.queue:
            raise AssertionError("fake_llm called with an empty queue")
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def fake_llm(monkeypatch):
    import core.llm  # noqa: F401  (Task 2 module; the fixture needs it to exist)

    fake = FakeLLM()
    monkeypatch.setattr("core.llm.call_json", fake)
    return fake


class FakeDB:
    """Records every q(sql, vars). Rows: set .rows[substring] = rows (first matching substring wins), else .default."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.rows: dict[str, list] = {}
        self.default: list = []

    async def q(self, sql: str, vars: dict | None = None) -> list:
        self.calls.append((sql, dict(vars or {})))
        for key, rows in self.rows.items():
            if key in sql:
                return rows(sql, vars or {}) if callable(rows) else rows
        return self.default

    def sql(self) -> list[str]:
        return [s for s, _ in self.calls]


@pytest.fixture
def fake_db(monkeypatch):
    import core.db

    fake = FakeDB()
    monkeypatch.setattr(core.db, "q", fake.q)
    return fake
