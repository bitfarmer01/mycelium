"""Feature 39 — shape-key drift detector.

A paraphrase of a question that classifies into a different question shape
fires a warning into the ledger feed. The one model call (classifying the
paraphrase) lives here, not in core.learn, which stays model-free; the
detector is injectable so tests and callers can classify without a model.
"""
from __future__ import annotations

from core import db, learn
from core.contracts import SHAPES

SYSTEM = """You classify questions for a retrieval system by their question shape.
The shapes, with their meaning:
- who-owns: ownership, responsibility, or accountability
- how-to: a procedure or steps
- policy: what is allowed, required, or forbidden
- when: timing, deadlines, or schedule
- definition: what something is or means
- comparison: how two or more things differ
- factual: any other single-fact lookup
Answer with exactly one shape."""

DRIFT_SCHEMA = {
    "type": "object",
    "properties": {"shape": {"type": "string", "enum": list(SHAPES)}},
    "required": ["shape"],
    "additionalProperties": False,
}


def is_drift(original_shape: str, paraphrase_shape: str) -> bool:
    """Pure check: two known shapes that differ mean drift; anything unknown does not."""
    return (original_shape in SHAPES and paraphrase_shape in SHAPES
            and original_shape != paraphrase_shape)


async def check(question_id: str, paraphrase: str, classify=None) -> dict:
    """Classify `paraphrase` against the question's stored shape; warn into the ledger feed on drift.

    classify: optional (paraphrase) -> shape callable; defaults to one cheap
    model call. The warning is a ledger row, so the console's /events feed
    surfaces it with no extra wiring.
    """
    row = learn._first(await db.q(f"SELECT id, shape, question FROM {learn._ref(question_id)}"))
    if not row or not row.get("id"):
        raise LookupError(f"unknown question {question_id!r}")
    original = str(row.get("shape") or "")
    if classify is None:
        from core import (
            llm,  # deferred: keeps the import graph light for non-drift paths
        )

        out = llm.call_json(SYSTEM, f"Question: {row.get('question') or ''}\nParaphrase: {paraphrase}",
                            DRIFT_SCHEMA, bucket="reflect")
        new_shape = str(out.get("shape") or "")
    else:
        new_shape = str(classify(paraphrase))
    drifted = is_drift(original, new_shape)
    ledger_id = None
    if drifted:
        ledger_id = await learn.write_ledger(learn.LedgerRow(
            target=str(row["id"]), target_kind="question", type="drift",
            delta=0.0, before=0.0, after=0.0,
            cause=f"shape-key drift: paraphrase classified as {new_shape}, original {original}"))
    return {"question_id": str(row["id"]), "original_shape": original,
            "paraphrase_shape": new_shape, "drifted": drifted, "ledger_id": ledger_id}
