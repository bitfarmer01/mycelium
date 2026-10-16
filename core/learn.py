"""Learning: Beta fitness per chunk/source, shape prior EMA, ledger rows, lessons, decay. No model calls."""
from __future__ import annotations

import re

from core import db
from core.contracts import CONFIG, ROLES, LedgerRow

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
    await db.q(f"UPSERT {db.rid('question_shape', shape)} SET shape = $shape, prior = $prior",
               {"shape": shape, "prior": after})
    lid = await write_ledger(LedgerRow(target=shape, target_kind="shape", type="prior",
                                       delta=after[idx] - before[idx], before=before[idx],
                                       after=after[idx], cause=cause))
    return before, after, lid


async def _chunk(cid: str) -> dict:
    row = _first(await db.q(f"SELECT * FROM {_ref(cid)}"))
    return {"alpha": float(row.get("alpha") or 1.0), "beta": float(row.get("beta") or 1.0),
            "source": row.get("source")}


async def _source_mean(src: str) -> tuple[float, float]:
    rows = await db.q(f"SELECT alpha, beta FROM chunk WHERE source = {_ref(src)}")
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

async def record_answer(question_id: str, shape: str, taken_role: str, chunk_ids: list[str]) -> None:
    qref = _ref(question_id)
    for cid in chunk_ids:
        await db.q(f"RELATE {qref}->cites->{_ref(cid)}")
    await _move_prior(shape, taken_role, CONFIG["prior_rate_uncorrected"], f"answered as {taken_role}")


async def correct(question_id: str, to_role: str) -> dict:
    _role_idx(to_role)
    qrow = _first(await db.q(f"SELECT * FROM {_ref(question_id)}"))
    shape = str(qrow.get("shape") or "factual")
    from_role = str(qrow.get("taken") or "")
    cause = f"correction {from_role}→{to_role}"
    obs_id = await _observation(question_id, "correct", from_role=from_role, to_role=to_role)
    before, after, prior_lid = await _move_prior(shape, to_role, CONFIG["prior_rate_correct"], cause)
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
    return {
        "shape": shape, "from_role": from_role, "to_role": to_role,
        "prior_before": before, "prior_after": after,
        "ledger_ids": [prior_lid, *lids], "lesson_id": lesson_id,
        "observation_id": obs_id, "chunk_ids": chunk_ids,
    }


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
    return n
