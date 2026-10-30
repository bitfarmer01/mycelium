"""Feature 37 — ledger compaction preserves the aggregates consumers use; dry-run reports first."""
from __future__ import annotations

import asyncio

from core import learn

OLD = "2026-09-01T00:00:00+00:00"  # pre-cutoff rows (anything before the compaction cutoff)
NEW = "2026-10-30T00:00:00+00:00"

OLD_ROWS = [
    {"id": "ledger:1", "target": "who-owns", "target_kind": "shape", "type": "prior",
     "cause": "correction literal→intent", "ts": OLD},
    {"id": "ledger:2", "target": "who-owns", "target_kind": "shape", "type": "prior",
     "cause": "correction intent→literal", "ts": OLD},
    {"id": "ledger:3", "target": "who-owns", "target_kind": "shape", "type": "prior",
     "cause": "correction literal→intent", "ts": OLD},
    {"id": "ledger:4", "target": "chunk:a", "target_kind": "chunk", "type": "confirm",
     "cause": "confirmed by user", "ts": OLD},
    {"id": "ledger:5", "target": "question:q1", "target_kind": "question", "type": "drift",
     "cause": "shape-key drift: paraphrase classified as how-to, original who-owns", "ts": OLD},
]

LIVE_PRIOR_ROWS = [
    {"cause": "correction how-to→scope"},  # a post-compaction correction still in the ledger
    {"cause": "answered as intent"},       # uncorrected prior move, never counted
]


def _seed(fake_db):
    fake_db.rows["FROM ledger WHERE ts < $c"] = [dict(r) for r in OLD_ROWS]
    fake_db.rows["SELECT total FROM ledger_agg"] = []
    fake_db.rows["UPSERT ledger_agg"] = []


def test_dry_run_reports_without_deleting(fake_db):
    _seed(fake_db)
    out = asyncio.run(learn.compact_ledger(30, dry_run=True))
    assert out["dry_run"] is True
    assert out["would_delete"] == 5
    assert not any(s.startswith("DELETE") for s in fake_db.sql())
    assert not any("ledger_agg" in s for s in fake_db.sql())  # nothing written either
    # aggregates are grouped per (target, target_kind, type, cause)
    by_cause = {a["cause"]: a["count"] for a in out["aggregates"]}
    assert by_cause["correction literal→intent"] == 2
    assert by_cause["confirmed by user"] == 1
    assert by_cause["shape-key drift: paraphrase classified as how-to, original who-owns"] == 1


def test_compaction_writes_aggregates_then_deletes(fake_db):
    _seed(fake_db)
    out = asyncio.run(learn.compact_ledger(30))
    assert out["dry_run"] is False and out["deleted"] == 5 and out["aggregates"] == 4
    deletes = [s for s in fake_db.sql() if s.startswith("DELETE ledger WHERE ts < $c")]
    assert deletes and deletes[0] == "DELETE ledger WHERE ts < $c"
    ups = [(s, v) for s, v in fake_db.calls if s.startswith("UPSERT ledger_agg")]
    assert len(ups) == 4
    totals = sorted(v["n"] for _, v in ups)
    assert totals == [1, 1, 1, 2]  # three singles + the doubled correction cause
    assert all(v["tk"] in ("shape", "chunk", "question") for _, v in ups)


def test_compaction_aggregates_accumulate(fake_db):
    """A second compaction run adds onto an existing aggregate instead of resetting it."""
    _seed(fake_db)
    fake_db.rows["SELECT total FROM ledger_agg"] = [{"total": 7}]
    out = asyncio.run(learn.compact_ledger(30))
    assert out["aggregates"] == 4
    ups = [(s, v) for s, v in fake_db.calls if s.startswith("UPSERT ledger_agg")]
    assert any(v["n"] == 9 for _, v in ups)  # 7 stored + 2 new for the doubled cause


def test_correction_count_survives_compaction(fake_db):
    fake_db.rows["FROM ledger WHERE target"] = [dict(r) for r in LIVE_PRIOR_ROWS]
    fake_db.rows["FROM ledger_agg WHERE"] = [
        {"cause": "correction literal→intent", "total": 3},
        {"cause": "correction intent→scope", "total": 2},
        {"cause": "prior decay 0.01/day over 28d", "total": 9},  # decay is not a correction
    ]
    assert asyncio.run(learn.correction_count("who-owns")) == 6  # 1 live + 3 + 2 compacted


def test_prior_snapshot_edit_count_includes_compacted(fake_db):
    fake_db.rows["FROM question_shape"] = [
        {"id": "question_shape:who-owns", "shape": "who-owns", "prior": [0.53, 0.3, 0.17]},
    ]
    fake_db.rows["target_kind = 'shape' AND type = 'prior' ORDER BY ts"] = [
        {"id": "ledger:9", "target": "who-owns", "delta": 0.1, "before": 0.3, "after": 0.4,
         "cause": "correction how-to→scope", "ts": NEW},
    ]
    fake_db.rows["FROM ledger_agg WHERE target_kind"] = [
        {"target": "who-owns", "cause": "correction literal→intent", "total": 3},
        {"target": "how-to", "cause": "correction literal→intent", "total": 5},  # other shape
    ]
    out = asyncio.run(learn.priors_snapshot())
    by_shape = {p["shape"]: p for p in out["priors"]}
    assert by_shape["who-owns"]["edit_count"] == 4  # 1 live + 3 compacted
    assert by_shape["who-owns"]["compacted_events"] == 3
    assert by_shape["who-owns"]["history"][0]["id"] == "ledger:9"
    assert by_shape["how-to"]["compacted_events"] == 5
    assert by_shape["factual"]["edit_count"] == 0
