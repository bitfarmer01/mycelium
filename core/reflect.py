"""Reflect: derive lessons from today's observations/candidates, then write row-cited sentences about today's lessons."""
from __future__ import annotations

import datetime as _dt
import json

from core import db
from core.contracts import REFLECT_SCHEMA

REFLECT_SYSTEM = (
    "You are reading rows of what changed today. Write at most five sentences under four headings: "
    "sources that rose or fell; questions the wiki could not answer; lessons written; priors or slot "
    "versions changed. Every sentence must cite at least one row id from the rows. Never add anything "
    "not in the rows."
)
EMPTY_SENTENCE = "I have not learned anything yet today."
MAX_SENTENCES = 5
DECIDED_STATUSES = ["vetoed", "vetoed_prelint", "scored", "promoted", "rejected", "accepted", "gated"]


def _since() -> str:
    """Today 00:00 UTC without a zone suffix, so string comparison works for both ISO forms."""
    today = _dt.datetime.now(_dt.timezone.utc).date()
    return f"{today.isoformat()}T00:00:00"


async def lessons_today() -> list[dict]:
    return await db.q(
        "SELECT * FROM lesson WHERE <string> ts >= $since ORDER BY ts",
        {"since": _since()},
    )


async def derive_lessons_from_observations() -> int:
    """Write one lesson for each of today's observations and each decided candidate no lesson references yet."""
    from core import learn  # lazy: Task 5 lands in parallel

    existing = await db.q("SELECT observation_ids, source_ids FROM lesson")
    referenced: set[str] = set()
    for row in existing:
        referenced.update(str(x) for x in (row.get("observation_ids") or []))
        referenced.update(str(x) for x in (row.get("source_ids") or []))

    observations = await db.q(
        "SELECT * FROM observation WHERE <string> ts >= $since ORDER BY ts", {"since": _since()}
    )
    candidates = await db.q("SELECT * FROM candidate WHERE status IN $decided", {"decided": DECIDED_STATUSES})

    created = 0
    for o in observations:
        oid = str(o.get("id"))
        if oid in referenced:
            continue
        kind = o.get("kind") or "observation"
        frm, to = o.get("from_role"), o.get("to_role")
        change = f"Observed {kind}" + (f" from {frm} to {to}" if frm or to else "")
        q = o.get("question_id") or o.get("question")
        rationale = f"on question {q}" if q else "recorded during today's questions"
        await learn.write_lesson(
            phase="observe", change=change, rationale=rationale, contributor="reflect",
            observation_ids=[oid], source_ids=[], kind=kind,
        )
        referenced.add(oid)
        created += 1

    for c in candidates:
        cid = str(c.get("id"))
        if cid in referenced:
            continue
        status = c.get("status")
        change = f"Candidate {c.get('name') or cid} ({c.get('axis') or 'unknown axis'}) {status}"
        rationale = c.get("veto_reason") or c.get("verdict") or f"status {status}"
        if not isinstance(rationale, str):
            rationale = json.dumps(rationale, default=str)
        await learn.write_lesson(
            phase="growth", change=change, rationale=rationale, contributor="reflect",
            observation_ids=[], source_ids=[cid], kind="candidate",
        )
        referenced.add(cid)
        created += 1
    return created


def render_fallback(rows: list[dict]) -> list[dict]:
    return [
        {"text": f"{r.get('change')}: {r.get('rationale')}", "row_ids": [str(r.get("id"))]}
        for r in rows[:MAX_SENTENCES]
    ]


def validate_sentences(sentences: list[dict], known_ids: set[str]) -> tuple[list[dict], int]:
    kept: list[dict] = []
    dropped = 0
    for s in sentences or []:
        ids = s.get("row_ids") if isinstance(s, dict) else None
        if isinstance(s, dict) and s.get("text") and ids and all(str(i) in known_ids for i in ids):
            kept.append({"text": s["text"], "row_ids": [str(i) for i in ids]})
        else:
            dropped += 1
    return kept, dropped


def _spoken(sentences: list[dict]) -> str:
    return " ".join(s["text"].strip() for s in sentences[:MAX_SENTENCES] if s.get("text"))


async def reflect() -> dict:
    await derive_lessons_from_observations()
    rows = await lessons_today()
    if not rows:
        sentences = [{"text": EMPTY_SENTENCE, "row_ids": []}]
        return {"sentences": sentences, "rows_read": 0, "dropped": 0, "llm": False, "spoken": _spoken(sentences)}

    from core import llm  # lazy: Task 2 lands in parallel

    err = getattr(llm, "LLMError", Exception)
    known = {str(r.get("id")) for r in rows}
    used_llm = True
    dropped = 0
    try:
        out = llm.call_json(REFLECT_SYSTEM, json.dumps(rows, default=str), REFLECT_SCHEMA, timeout_s=30)
        sentences, dropped = validate_sentences((out or {}).get("sentences") or [], known)
        sentences = sentences[:MAX_SENTENCES]
    except err:
        used_llm = False
        sentences = []
    if not sentences:
        sentences = render_fallback(rows)
    return {
        "sentences": sentences,
        "rows_read": len(rows),
        "dropped": dropped,
        "llm": used_llm,
        "spoken": _spoken(sentences),
    }
