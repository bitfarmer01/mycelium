"""Exam history: the persisted exam record read back out in order (feature 41).

The write side is growth.improve(): every burst persists one candidate row per
candidate (status, verdict, fitness delta/mean/spread) plus gate/veto/promote ledger
events — the candidate, the fitness deltas, and the decision. This module reads that
record back for /exam/history. Scheduled bursts and the burst cost ceiling land in
this module in the following commits.
"""
from __future__ import annotations

from core import db

HISTORY_LIMIT_MAX = 500
LEDGER_SCAN_LIMIT = 1000

CANDIDATE_FIELDS = ("name", "axis", "status", "veto_reason", "verdict", "mean", "spread", "delta",
                    "ratio", "sandbox_id", "runner", "slot_hash", "ts")


async def history(limit: int = 100) -> list[dict]:
    """Exam record oldest -> newest: one entry per persisted candidate with its ledger decisions attached."""
    limit = max(1, min(int(limit), HISTORY_LIMIT_MAX))
    rows = await db.q(f"SELECT * FROM candidate ORDER BY ts DESC LIMIT {limit}")
    ledger_rows = await db.q(f"SELECT * FROM ledger ORDER BY ts DESC LIMIT {LEDGER_SCAN_LIMIT}")
    # candidate-prefixed targets are filtered client-side: works on any SurrealQL build and on the fake
    by_candidate: dict[str, list[dict]] = {}
    for ev in reversed(ledger_rows):  # reverse the DESC scan back into ascending time
        target = str(ev.get("target", ""))
        if not target.startswith("candidate:"):
            continue
        by_candidate.setdefault(target.removeprefix("candidate:"), []).append(ev)

    out = []
    for row in reversed(rows):  # DESC scan -> ascending order
        rec = {k: row.get(k) for k in CANDIDATE_FIELDS if k in row}
        rec["events"] = by_candidate.get(str(row.get("name", "")), [])
        out.append(rec)
    return out
