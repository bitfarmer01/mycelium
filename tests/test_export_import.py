"""Feature 34 — export → wipe → import reproduces identical priors, fitness, and ledger counts."""
from __future__ import annotations

import asyncio

import pytest

from core import learn


class MemDB:
    """In-memory SurrealDB emulation covering exactly the SQL export/import issues."""

    def __init__(self):
        self.tables: dict[str, dict[str, dict]] = {
            "question_shape": {}, "chunk": {}, "correction": {}, "ledger": {},
        }
        self.sqls: list[str] = []

    async def q(self, sql: str, vars: dict | None = None) -> list:
        self.sqls.append(sql)
        s = " ".join(sql.split())
        v = vars or {}
        if s.startswith("REMOVE TABLE"):
            self.tables[s.split()[-1]] = {}
            return []
        if s.startswith("UPDATE chunk"):
            for r in self.tables["chunk"].values():
                r["alpha"], r["beta"] = 1, 1
            return []
        if s.startswith(("CREATE ", "UPSERT ")) and "CONTENT $row" in s:
            ref = s.split()[1]
            table, key = ref.split(":", 1)
            key = key.strip("⟨⟩")
            self.tables[table][key] = {**v["row"], "id": f"{table}:{key}"}
            return [dict(self.tables[table][key])]
        if s.startswith("UPSERT chunk:") and "SET " in s:
            key = s.split()[1].split(":", 1)[1]
            row = self.tables["chunk"][key]
            row["alpha"], row["beta"], row["last_event"] = v["alpha"], v["beta"], v["ev"]
            return [dict(row)]
        if s.startswith("SELECT"):
            if "FROM question_shape" in s:
                return [dict(r) for r in self.tables["question_shape"].values()]
            if "FROM chunk" in s:
                return [dict(r) for r in self.tables["chunk"].values()]
            if "FROM correction" in s:
                return [dict(r) for r in self.tables["correction"].values()]
            if "FROM ledger" in s:
                return sorted((dict(r) for r in self.tables["ledger"].values()),
                              key=lambda r: str(r.get("ts", "")))
        raise AssertionError(f"unexpected sql: {sql}")


@pytest.fixture
def mem_db(monkeypatch):
    mem = MemDB()
    monkeypatch.setattr("core.db.q", mem.q)
    return mem


def _seed(mem: MemDB) -> None:
    mem.tables["question_shape"] = {
        "who-owns": {"id": "question_shape:who-owns", "shape": "who-owns",
                     "prior": [0.53, 0.3, 0.17], "prior_ts": "2026-10-01T00:00:00+00:00"},
        "how-to": {"id": "question_shape:how-to", "shape": "how-to",
                   "prior": [0.2, 0.6, 0.2], "prior_ts": "2026-10-02T00:00:00+00:00"},
    }
    mem.tables["chunk"] = {
        "a": {"id": "chunk:a", "alpha": 5.0, "beta": 1.0, "last_event": "2026-10-03T00:00:00+00:00",
              "text": "corpus text A", "vector": [0.1, 0.2]},
        "b": {"id": "chunk:b", "alpha": 1.0, "beta": 3.5, "last_event": None,
              "text": "corpus text B", "vector": [0.3, 0.4]},
    }
    mem.tables["correction"] = {
        "1": {"id": "correction:1", "question": "question:q1", "shape": "who-owns",
              "to_role": "intent", "actor": "rajath", "undone": False},
    }
    mem.tables["ledger"] = {
        "1": {"id": "ledger:1", "target": "who-owns", "target_kind": "shape", "type": "prior",
              "ts": "2026-10-01T00:00:00+00:00"},
        "2": {"id": "ledger:2", "target": "chunk:a", "target_kind": "chunk", "type": "correct",
              "ts": "2026-10-01T01:00:00+00:00"},
        "3": {"id": "ledger:3", "target": "chunk:b", "target_kind": "chunk", "type": "confirm",
              "ts": "2026-10-02T00:00:00+00:00"},
    }


def _snapshot(mem: MemDB) -> dict:
    return {
        "priors": {k: list(v["prior"]) for k, v in mem.tables["question_shape"].items()},
        "fitness": {k: v["alpha"] / (v["alpha"] + v["beta"]) for k, v in mem.tables["chunk"].items()},
        "ledger_count": len(mem.tables["ledger"]),
        "correction_count": len(mem.tables["correction"]),
    }


def test_export_wipe_import_reproduces_identical_state(mem_db):
    _seed(mem_db)
    before = _snapshot(mem_db)

    state = asyncio.run(learn.export_state())
    assert state["counts"] == {"question_shape": 2, "chunk": 2, "correction": 1, "ledger": 3}

    # simulate drift while the export was in hand
    mem_db.tables["question_shape"]["who-owns"]["prior"] = [1 / 3] * 3
    mem_db.tables["chunk"]["a"]["alpha"], mem_db.tables["chunk"]["a"]["beta"] = 1, 1
    mem_db.tables["ledger"].clear()

    out = asyncio.run(learn.import_state(state, wipe=True))
    assert out["restored"] == {"question_shape": 2, "correction": 1, "ledger": 3, "chunk": 2}

    after = _snapshot(mem_db)
    assert after["priors"] == before["priors"]
    assert after["fitness"] == before["fitness"]  # exact floats, not approx
    assert after["ledger_count"] == before["ledger_count"]
    assert after["correction_count"] == before["correction_count"]


def test_import_preserves_ids_and_corpus_fields(mem_db):
    _seed(mem_db)
    state = asyncio.run(learn.export_state())
    asyncio.run(learn.import_state(state, wipe=True))

    # undo targets and ledger references stay valid: original record ids restored
    assert "1" in mem_db.tables["correction"] and mem_db.tables["correction"]["1"]["actor"] == "rajath"
    assert {r["id"] for r in mem_db.tables["ledger"].values()} == {
        "ledger:1", "ledger:2", "ledger:3"}
    # the wipe never touches corpus content: text and embeddings survive the roundtrip
    assert mem_db.tables["chunk"]["a"]["text"] == "corpus text A"
    assert mem_db.tables["chunk"]["a"]["vector"] == [0.1, 0.2]
    assert mem_db.tables["chunk"]["a"]["alpha"] == 5.0
    # and the wipe sequence is exactly the learning tables — never REMOVE chunk
    removes = [s for s in mem_db.sqls if s.startswith("REMOVE TABLE")]
    assert removes == ["REMOVE TABLE ledger", "REMOVE TABLE correction",
                       "REMOVE TABLE question_shape"]


def test_import_without_wipe_is_idempotent(mem_db):
    _seed(mem_db)
    state = asyncio.run(learn.export_state())
    asyncio.run(learn.import_state(state, wipe=False))
    asyncio.run(learn.import_state(state, wipe=False))  # run twice: same result
    snap = _snapshot(mem_db)
    assert snap["ledger_count"] == 3 and snap["correction_count"] == 1


def test_import_ignores_malformed_rows(mem_db):
    state = {"question_shape": [{"shape": "who-owns"}], "chunk": ["junk"],
             "correction": [], "ledger": [], "exported_at": "t"}
    out = asyncio.run(learn.import_state(state, wipe=False))
    assert out["restored"] == {"question_shape": 0, "correction": 0, "ledger": 0, "chunk": 0}
