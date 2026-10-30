"""Learning: Beta fitness per chunk/source, shape prior EMA, ledger rows, lessons, decay. No model calls."""
from __future__ import annotations

import datetime as _dt
import hashlib
import re

from core import db
from core.contracts import CONFIG, ROLES, SHAPES, LedgerRow

_ID = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.+)$")


# ---------- pure maths ----------

def beta_update(alpha: float, beta: float, evidence: float) -> tuple[float, float]:
    e = min(1.0, max(0.0, float(evidence)))
    if e > 0.5:
        alpha += 2 * (e - 0.5)
    else:
        beta += 2 * (0.5 - e)
    return alpha, beta


def fitness(alpha: float, beta: float) -> float:
    return alpha / (alpha + beta)


def uncertainty(alpha: float, beta: float) -> float:
    s = alpha + beta
    return (alpha * beta / (s * s * (s + 1))) / 0.25


def decay_ab(alpha: float, beta: float, decay: float | None = None) -> tuple[float, float]:
    d = CONFIG["decay"] if decay is None else decay
    return 1 + d * (alpha - 1), 1 + d * (beta - 1)


def prior_update(prior: list[float], role_idx: int, rate: float) -> list[float]:
    p = [pi + rate * ((1.0 if i == role_idx else 0.0) - pi) for i, pi in enumerate(prior)]
    floor = CONFIG["prior_floor"]
    s = sum(p)
    p = [x / s for x in p]
    fixed: set[int] = set()
    while True:  # pin entries below floor, rescale the rest to fill the remaining mass
        low = {i for i, x in enumerate(p) if i not in fixed and x < floor}
        if not low:
            break
        fixed |= low
        free = [i for i in range(len(p)) if i not in fixed]
        rest = 1.0 - floor * len(fixed)
        fs = sum(p[i] for i in free)
        p = [floor if i in fixed else (p[i] / fs * rest if fs > 0 else rest / max(1, len(free)))
             for i in range(len(p))]
    return p


# ---------- helpers ----------

def _ref(record_id: str) -> str:
    """Validated record-id literal for interpolation (avoids type::thing/type::record version drift)."""
    m = _ID.match(str(record_id))
    if not m:
        raise ValueError(f"bad record id: {record_id!r}")
    key = m.group(2)
    if key.startswith("⟨") and key.endswith("⟩"):
        key = key[1:-1]
    return db.rid(m.group(1), key)


def _first(rows: list) -> dict:
    return rows[0] if rows and isinstance(rows[0], dict) else {}


def _role_idx(role: str) -> int:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    return ROLES.index(role)


def _ids(v) -> list[str]:
    out: list[str] = []
    if isinstance(v, (list, tuple)):
        for x in v:
            out.extend(_ids(x))
    elif isinstance(v, dict):
        if "c" in v:
            out.extend(_ids(v["c"]))
        elif "id" in v:
            out.append(str(v["id"]))
    elif v:
        out.append(str(v))
    return out


async def _cited_chunks(question_id: str, qrow: dict) -> list[str]:
    ids: list[str] = []
    try:
        ids = _ids(await db.q(f"SELECT ->cites->chunk AS c FROM {_ref(question_id)}"))
    except Exception:
        ids = []
    if not ids:
        ids = _ids(qrow.get("chunk_ids") or [])
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# ---------- DB writes ----------

async def write_ledger(row: LedgerRow) -> str:
    content = {**row.to_dict(), "ts": db.now()}
    rows = await db.q("CREATE ledger CONTENT $row", {"row": content})
    return str(_first(rows).get("id", ""))


async def write_lesson(phase: str, change: str, rationale: str, contributor: str,
                       observation_ids: list[str], source_ids: list[str], kind: str) -> str:
    content = {
        "phase": phase, "change": change, "rationale": rationale, "contributor": contributor,
        "ts": db.now(), "source_ids": list(source_ids), "observation_ids": list(observation_ids),
        "kind": kind,
    }
    rows = await db.q("CREATE lesson CONTENT $row", {"row": content})
    return str(_first(rows).get("id", ""))


async def get_prior(shape: str) -> list[float]:
    ref = db.rid("question_shape", shape)
    row = _first(await db.q(f"SELECT prior FROM {ref}"))
    prior = row.get("prior")
    if isinstance(prior, list) and len(prior) == len(ROLES):
        return [float(x) for x in prior]
    prior = [1 / len(ROLES)] * len(ROLES)
    await db.q(f"UPSERT {ref} SET shape = $shape, prior = $prior", {"shape": shape, "prior": prior})
    return prior


async def _move_prior(shape: str, role: str, rate: float, cause: str) -> tuple[list[float], list[float], str]:
    idx = _role_idx(role)
    before = await get_prior(shape)
    after = prior_update(before, idx, rate)
    await db.q(f"UPSERT {db.rid('question_shape', shape)} SET shape = $shape, prior = $prior, prior_ts = $ts",
               {"shape": shape, "prior": after, "ts": db.now()})
    lid = await write_ledger(LedgerRow(target=shape, target_kind="shape", type="prior",
                                       delta=after[idx] - before[idx], before=before[idx],
                                       after=after[idx], cause=cause))
    return before, after, lid


async def _chunk(cid: str) -> dict:
    row = _first(await db.q(f"SELECT * FROM {_ref(cid)}"))
    return {"alpha": float(row.get("alpha") or 1.0), "beta": float(row.get("beta") or 1.0),
            "source": row.get("source") or (row.get("metadata") or {}).get("source")}


async def _source_mean(src: str) -> tuple[float, float]:
    rows = await db.q(f"SELECT alpha, beta FROM chunk WHERE source = {_ref(src)} OR metadata.source = $s",
                      {"s": str(src)})
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return 1.0, 1.0
    a = sum(float(r.get("alpha") or 1.0) for r in rows) / len(rows)
    b = sum(float(r.get("beta") or 1.0) for r in rows) / len(rows)
    return a, b


async def _apply_evidence(chunk_ids: list[str], evidence: float, kind: str, cause: str) -> tuple[list[str], list[str]]:
    """Beta-update each chunk and its source(s); one ledger row per chunk and per source."""
    ledger_ids: list[str] = []
    chunks = {cid: await _chunk(cid) for cid in chunk_ids}
    sources = []
    for c in chunks.values():
        s = c["source"]
        if s and _ID.match(str(s)) and str(s) not in sources:
            sources.append(str(s))
    src_before = {s: await _source_mean(s) for s in sources}
    ts = db.now()
    for cid, c in chunks.items():
        a, b = beta_update(c["alpha"], c["beta"], evidence)
        f0, f1 = fitness(c["alpha"], c["beta"]), fitness(a, b)
        await db.q(f"UPDATE {_ref(cid)} SET alpha = $alpha, beta = $beta, last_event = $ts",
                   {"alpha": a, "beta": b, "ts": ts})
        ledger_ids.append(await write_ledger(LedgerRow(cid, "chunk", kind, f1 - f0, f0, f1, cause)))
    for s in sources:
        a, b = await _source_mean(s)
        f0, f1 = fitness(*src_before[s]), fitness(a, b)
        await db.q(f"UPDATE {_ref(s)} SET alpha = $alpha, beta = $beta", {"alpha": a, "beta": b})
        ledger_ids.append(await write_ledger(LedgerRow(s, "source", kind, f1 - f0, f0, f1, cause)))
    return ledger_ids, sources


async def _observation(question_id: str, kind: str, **extra) -> str:
    content = {"question": question_id, "kind": kind, "ts": db.now(), **extra}
    rows = await db.q("CREATE observation CONTENT $row", {"row": content})
    return str(_first(rows).get("id", ""))


# ---------- public flows ----------

def correction_rate(confidence: float) -> float:
    """A correction's confidence in [0,1] scales the pinned correction rate; 1.0 is the unweighted behavior."""
    c = min(1.0, max(0.0, float(confidence)))
    return CONFIG["prior_rate_correct"] * c


async def record_answer(question_id: str, shape: str, taken_role: str, chunk_ids: list[str]) -> None:
    qref = _ref(question_id)
    for cid in chunk_ids:
        await db.q(f"RELATE {qref}->cites->{_ref(cid)}")
    await _move_prior(shape, taken_role, CONFIG["prior_rate_uncorrected"], f"answered as {taken_role}")


async def correct(question_id: str, to_role: str, confidence: float = 1.0, actor: str = "") -> dict:
    _role_idx(to_role)
    conf = min(1.0, max(0.0, float(confidence)))
    qrow = _first(await db.q(f"SELECT * FROM {_ref(question_id)}"))
    shape = str(qrow.get("shape") or "factual")
    from_role = str(qrow.get("taken") or "")
    cause = f"correction {from_role}→{to_role}"
    obs_id = await _observation(question_id, "correct", from_role=from_role, to_role=to_role,
                                confidence=conf, actor=actor)
    before, after, prior_lid = await _move_prior(shape, to_role, correction_rate(conf), cause)
    chunk_ids = await _cited_chunks(question_id, qrow)
    lids, sources = await _apply_evidence(chunk_ids, CONFIG["beta_evidence_correct"], "correct", cause)
    await db.q(f"UPDATE {_ref(question_id)} SET corrected_to = $r", {"r": to_role})
    lesson_id = await write_lesson(
        phase="correction",
        change=f"{shape}: prior moved toward {to_role}; {len(chunk_ids)} cited chunk(s) down-weighted",
        rationale=f"user corrected reading {from_role}→{to_role}",
        contributor="user",
        observation_ids=[obs_id] if obs_id else [],
        source_ids=sources,
        kind="correct",
    )
    # feature 38: an undoable record of the exact prior delta this correction applied
    rows = await db.q("CREATE correction CONTENT $row", {"row": {
        "question": question_id, "shape": shape, "to_role": to_role, "actor": actor,
        "confidence": conf, "prior_before": before, "prior_after": after,
        "prior_ledger": prior_lid, "ts": db.now()}})
    correction_id = str(_first(rows).get("id", ""))
    return {
        "shape": shape, "from_role": from_role, "to_role": to_role,
        "prior_before": before, "prior_after": after,
        "ledger_ids": [prior_lid, *lids], "lesson_id": lesson_id,
        "observation_id": obs_id, "chunk_ids": chunk_ids,
        "correction_id": correction_id, "actor": actor, "confidence": conf,
    }


async def undo(correction_id: str, actor: str) -> dict:
    """Revert the exact prior delta a correction applied; only its actor may call it (feature 38).

    Undo is refused unless the prior is still exactly where the correction left it —
    an inexact rewind of an EMA chain would silently erase later learning.
    """
    ref = _ref(correction_id)
    row = _first(await db.q(f"SELECT * FROM {ref}"))
    if not isinstance(row, dict) or not str(row.get("id") or "").startswith("correction:"):
        raise LookupError(f"unknown correction {correction_id!r}")
    if str(row.get("actor") or "") != str(actor):
        raise PermissionError("only the actor of a correction may undo it")
    if row.get("undone"):
        raise ValueError(f"correction {row['id']} was already undone")
    shape = str(row.get("shape") or "")
    to_role = str(row.get("to_role") or "")
    idx = _role_idx(to_role) if to_role else 0
    current = await get_prior(shape)
    restored = [float(x) for x in row.get("prior_before") or []]
    applied = [float(x) for x in row.get("prior_after") or []]
    if len(restored) != len(ROLES) or any(abs(c - a) > 1e-9 for c, a in zip(current, applied)):
        raise ValueError("prior has moved since this correction; refusing an inexact undo")
    await db.q(f"UPSERT {db.rid('question_shape', shape)} SET shape = $shape, prior = $prior",
               {"shape": shape, "prior": restored})
    lid = await write_ledger(LedgerRow(target=shape, target_kind="shape", type="prior",
                                       delta=restored[idx] - current[idx], before=current[idx],
                                       after=restored[idx], cause=f"undo {row['id']}"))
    await db.q(f"UPDATE {ref} SET undone = $u", {"u": True})
    return {"correction_id": str(row["id"]), "shape": shape,
            "prior_before": current, "prior_after": restored, "ledger_id": lid}


async def correction_count(shape: str) -> int:
    """Cross-session attribution badge: the shape's ledger prior rows caused by corrections (feature 36).

    Compacted (feature 37) rows survive here as ledger_agg totals, so the badge
    count is the same number it was before compaction.
    """
    rows = await db.q("SELECT cause FROM ledger WHERE target = $shape AND target_kind = 'shape' AND type = 'prior'",
                      {"shape": shape})
    live_n = sum(1 for r in rows if isinstance(r, dict) and str(r.get("cause") or "").startswith("correction"))
    aggs = await db.q("SELECT cause, total FROM ledger_agg WHERE target = $shape AND target_kind = 'shape' AND type = 'prior'",
                      {"shape": shape})
    agg_n = sum(int(r.get("total") or 0) for r in aggs if isinstance(r, dict)
                and str(r.get("cause") or "").startswith("correction"))
    return live_n + agg_n


async def confirm(question_id: str) -> dict:
    qrow = _first(await db.q(f"SELECT * FROM {_ref(question_id)}"))
    obs_id = await _observation(question_id, "confirm")
    chunk_ids = await _cited_chunks(question_id, qrow)
    lids, sources = await _apply_evidence(chunk_ids, CONFIG["beta_evidence_confirm"], "confirm",
                                          "confirmed by user")
    return {"ledger_ids": lids, "observation_id": obs_id, "chunk_ids": chunk_ids, "source_ids": sources}


async def decay_tick() -> int:
    cfg = _first(await db.q("SELECT last_tick FROM config:main"))
    last = cfg.get("last_tick")
    rows = await db.q("SELECT id, alpha, beta, last_event FROM chunk WHERE alpha + beta > 2")
    n = 0
    for r in rows:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        ev = r.get("last_event")
        if last and ev and str(ev) >= str(last):
            continue
        a0, b0 = float(r.get("alpha") or 1.0), float(r.get("beta") or 1.0)
        if a0 + b0 <= 2:
            continue
        a, b = decay_ab(a0, b0)
        f0, f1 = fitness(a0, b0), fitness(a, b)
        await db.q(f"UPDATE {_ref(r['id'])} SET alpha = $alpha, beta = $beta", {"alpha": a, "beta": b})
        await write_ledger(LedgerRow(str(r["id"]), "chunk", "decay", f1 - f0, f0, f1, "decay tick"))
        n += 1
    await db.q("UPSERT config:main SET last_tick = $ts", {"ts": db.now()})
    return n + await decay_priors()


# ---------- prior decay toward uniform (feature 33) ----------

def prior_decay(prior: list[float], days: float, rate: float | None = None) -> list[float]:
    """Move a shape prior toward uniform, compounded per elapsed day (feature 33).

    rate 0.0 (the CONFIG default) is off until tuned; 0.01 is the spec's 1%/day.
    After 28 days at 1%/day a prior retains (1 - 0.01)^28 ≈ 0.7547 of its
    deviation from uniform — the documented factor.
    """
    d = CONFIG["prior_decay_per_day"] if rate is None else rate
    if not d:  # off by default until tuned
        return list(prior)
    days = max(0.0, float(days))
    retain = (1.0 - min(1.0, max(0.0, float(d)))) ** days
    uniform = 1 / len(prior)
    return [uniform + (x - uniform) * retain for x in prior]


def _age_days(ts, now: str | None = None) -> float:
    """Elapsed days since an ISO timestamp; unparseable or missing counts as 0 (never decays)."""
    if not ts:
        return 0.0
    try:
        t = _dt.datetime.fromisoformat(str(ts))  # py3.12+ parses a trailing Z natively
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.UTC)
        n = _dt.datetime.fromisoformat(str(now)) if now else _dt.datetime.now(_dt.UTC)
        return max(0.0, (n - t).total_seconds() / 86400)
    except ValueError:
        return 0.0


async def decay_priors(rate: float | None = None, now: str | None = None) -> int:
    """Age every stored shape prior toward uniform by the daily rate (feature 33).

    A no-op (and a DB no-op) while the rate is 0. Each decayed prior gets one
    ledger decay row so the feed shows the aging, same as chunk decay ticks.
    """
    d = CONFIG["prior_decay_per_day"] if rate is None else rate
    if not d:
        return 0
    rows = [r for r in await db.q("SELECT id, shape, prior, prior_ts FROM question_shape")
            if isinstance(r, dict)]
    n = 0
    for r in rows:
        shape = str(r.get("shape") or "")
        if not shape:
            continue
        prior = [float(x) for x in r.get("prior") or []]
        if len(prior) != len(ROLES):
            continue
        days = _age_days(r.get("prior_ts"), now)
        after = prior_decay(prior, days, d)
        if all(abs(a - b) <= 1e-12 for a, b in zip(prior, after)):
            continue
        idx = max(range(len(prior)), key=lambda i: abs(prior[i] - 1 / len(ROLES)))
        ts = db.now()
        await db.q(f"UPSERT {db.rid('question_shape', shape)} SET shape = $shape, prior = $prior, prior_ts = $ts",
                   {"shape": shape, "prior": after, "ts": ts})
        await write_ledger(LedgerRow(target=shape, target_kind="shape", type="decay",
                                     delta=after[idx] - prior[idx], before=prior[idx],
                                     after=after[idx],
                                     cause=f"prior decay {d}/day over {days:.0f}d"))
        n += 1
    return n


# ---------- learning-state export/import (feature 34) ----------

async def export_state() -> dict:
    """Full learning state for backup/restore: priors, Beta params, corrections, ledger (feature 34)."""
    tables = {
        "question_shape": await db.q("SELECT id, shape, prior, prior_ts FROM question_shape"),
        "chunk": await db.q("SELECT id, alpha, beta, last_event FROM chunk"),
        "correction": await db.q("SELECT * FROM correction"),
        "ledger": await db.q("SELECT * FROM ledger ORDER BY ts"),
    }
    out = {t: [r for r in rows if isinstance(r, dict)] for t, rows in tables.items()}
    out["version"] = 1
    out["exported_at"] = db.now()
    out["counts"] = {t: len(rows) for t, rows in out.items() if isinstance(rows, list)}
    return out


async def import_state(state: dict, wipe: bool = True) -> dict:
    """Restore a learning-state export, wiping current state first unless wipe=False (feature 34).

    The wipe clears the learning tables but never the corpus: chunk rows are
    corpus content, so only their learning params (alpha/beta) reset, then the
    export's values restore on top. Rows recreate under their original ids so
    undo targets and ledger references stay valid.
    """
    if wipe:
        await db.q("REMOVE TABLE ledger")
        await db.q("REMOVE TABLE correction")
        await db.q("REMOVE TABLE question_shape")
        await db.q("UPDATE chunk SET alpha = 1, beta = 1")
    restored: dict[str, int] = {}
    for table in ("question_shape", "correction", "ledger", "chunk"):
        n = 0
        for row in state.get(table) or []:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "")
            if ":" not in rid:
                continue
            ref = _ref(rid)
            if table == "chunk":
                # field-wise UPSERT: chunk rows are corpus content — text and
                # embeddings must survive the restore untouched
                await db.q(f"UPSERT {ref} SET alpha = $alpha, beta = $beta, last_event = $ev",
                           {"alpha": row.get("alpha", 1.0), "beta": row.get("beta", 1.0),
                            "ev": row.get("last_event")})
            else:
                content = {k: v for k, v in row.items() if k != "id"}
                await db.q(f"UPSERT {ref} CONTENT $row", {"row": content})
            n += 1
        restored[table] = n
    return {"restored": restored, "exported_at": state.get("exported_at")}


# ---------- ledger compaction (feature 37) ----------

async def compact_ledger(days: int, dry_run: bool = False) -> dict:
    """Fold ledger rows older than `days` into per-cause aggregates, then delete them (feature 37).

    Every deleted row's (target, target_kind, type, cause) group lands in the
    ledger_agg table, so the counts downstream consumers read — correction
    attribution, prior edit history — survive compaction. dry_run=True reports
    what would be deleted without touching anything.
    """
    cutoff = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=days)).isoformat()
    old = [r for r in await db.q("SELECT id, target, target_kind, type, cause, ts FROM ledger WHERE ts < $c",
                                 {"c": cutoff}) if isinstance(r, dict)]
    groups: dict[tuple[str, str, str, str], dict] = {}
    for r in old:
        key = (str(r.get("target")), str(r.get("target_kind")), str(r.get("type")), str(r.get("cause")))
        g = groups.setdefault(key, {"count": 0, "first_ts": r.get("ts"), "last_ts": r.get("ts")})
        g["count"] += 1
        ts = str(r.get("ts") or "")
        if ts < str(g["first_ts"] or ""):
            g["first_ts"] = ts
        if ts > str(g["last_ts"] or ""):
            g["last_ts"] = ts
    aggregates = [dict(target=k[0], target_kind=k[1], type=k[2], cause=k[3], **v)
                  for k, v in groups.items()]
    if dry_run:
        return {"dry_run": True, "cutoff": cutoff, "would_delete": len(old), "aggregates": aggregates}
    for agg in aggregates:
        h = hashlib.sha1("|".join([agg["target"], agg["target_kind"], agg["type"], agg["cause"]])
                         .encode()).hexdigest()[:16]
        ref = db.rid("ledger_agg", h)
        rows = await db.q(f"SELECT total FROM {ref}")
        prev = int(rows[0]["total"]) if rows and isinstance(rows[0], dict) and rows[0].get("total") else 0
        await db.q(f"UPSERT {ref} SET target = $t, target_kind = $tk, type = $ty, cause = $c, "
                   "total = $n, first_ts = $f, last_ts = $l",
                   {"t": agg["target"], "tk": agg["target_kind"], "ty": agg["type"],
                    "c": agg["cause"], "n": prev + agg["count"],
                    "f": agg["first_ts"], "l": agg["last_ts"]})
    await db.q("DELETE ledger WHERE ts < $c", {"c": cutoff})
    return {"dry_run": False, "cutoff": cutoff, "deleted": len(old), "aggregates": len(aggregates)}



# ---------- prior dashboard (feature 31) ----------

async def priors_snapshot() -> dict:
    """Per-shape prior values with the ledger edit history behind them, straight from the DB rows."""
    rows = [r for r in await db.q("SELECT id, shape, prior FROM question_shape") if isinstance(r, dict)]
    events = [r for r in await db.q(
        "SELECT id, target, delta, before, after, cause, ts FROM ledger"
        " WHERE target_kind = 'shape' AND type = 'prior' ORDER BY ts") if isinstance(r, dict)]
    # feature 37: compacted prior events survive as per-cause totals — they keep
    # counting toward edit history even though their rows are gone
    aggs = [r for r in await db.q(
        "SELECT target, cause, total FROM ledger_agg WHERE target_kind = 'shape' AND type = 'prior'")
        if isinstance(r, dict)]
    stored = {str(r.get("shape")): r for r in rows if r.get("shape")}
    uniform = [1 / len(ROLES)] * len(ROLES)
    out = []
    for shape in SHAPES:
        row = stored.get(shape)
        raw = row.get("prior") if row else None
        prior = [float(x) for x in raw] if isinstance(raw, list) and len(raw) == len(ROLES) else uniform
        history = [h for h in events if str(h.get("target")) == shape]
        compacted = sum(int(a.get("total") or 0) for a in aggs
                        if str(a.get("target")) == shape
                        and str(a.get("cause") or "").startswith("correction"))
        out.append({"shape": shape, "prior": prior, "edit_count": len(history) + compacted,
                    "compacted_events": compacted, "history": history})
    return {"roles": list(ROLES), "priors": out}
