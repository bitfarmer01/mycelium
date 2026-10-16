"""Reflex: three readings of a typed question, prior-weighted selection, cited synthesis, spoken tier."""
from __future__ import annotations

import asyncio
import inspect
import math
import re
import time
from typing import Any

from core.contracts import CONFIG, READINGS_SCHEMA, ROLES, SYNTHESIS_SCHEMA

READINGS_SYSTEM = (
    "Return three readings of the question with fixed roles literal, intent, scope; probabilities sum to 1; "
    "choose taken; give why and the one flip_condition fact that would change it."
)

FALLBACK_SPOKEN = "I found passages but could not compose an answer."

_SHAPE_RULES = [
    ("who-owns", r"\b(who|owner|owns|responsible)\b"),
    ("how-to", r"\b(how do|how to|process|steps)\b"),
    ("policy", r"\b(policy|allowed|rule|rules)\b"),
    ("when", r"\b(when|deadline|how long)\b"),
    ("definition", r"\b(what is|what's a|define|meaning)\b"),
    ("comparison", r"\b(compare|versus|vs\.?|difference)\b"),
]

_CITE_RE = re.compile(r"\s*\[(\d+)\]")
_ANY_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]")
_SUFFIX_RE = re.compile(r"\s*I read that as\b.*$", re.IGNORECASE | re.DOTALL)


def _llm_error() -> type[BaseException]:
    try:
        from core import llm

        return getattr(llm, "LLMError", Exception)
    except Exception:
        return Exception


async def _call(fn, *args, **kwargs):
    """Call a sibling function that may be sync or async; sync calls run off the event loop."""
    if inspect.iscoroutinefunction(fn):
        res = await fn(*args, **kwargs)
    else:
        res = await asyncio.to_thread(fn, *args, **kwargs)
    if inspect.isawaitable(res):
        res = await res
    return res


def classify_shape(text: str) -> str:
    t = (text or "").lower()
    for shape, pat in _SHAPE_RULES:
        if re.search(pat, t):
            return shape
    return "factual"


def _literal_only(text: str, shape: str) -> dict:
    return {
        "shape": shape,
        "readings": [{"role": "literal", "paraphrase": text, "assumption": "", "query": text, "p": 1.0}],
        "taken": "literal",
        "why": "Readings were unavailable, so the question is taken literally.",
        "flip_condition": "",
    }


async def readings(text: str) -> dict:
    from core import llm

    err = _llm_error()
    shape_guess = classify_shape(text)
    try:
        out = await _call(llm.call_json, READINGS_SYSTEM, text, READINGS_SCHEMA)
    except err:
        return _literal_only(text, shape_guess)
    if not isinstance(out, dict):
        return _literal_only(text, shape_guess)
    rs = [dict(r) for r in (out.get("readings") or []) if isinstance(r, dict) and r.get("role") in ROLES]
    if not rs:
        return _literal_only(text, shape_guess)
    ps = [max(0.0, float(r.get("p") or 0.0)) for r in rs]
    total = sum(ps)
    for r, p in zip(rs, ps):
        r["p"] = p / total if total > 0 else 1.0 / len(rs)
        r.setdefault("paraphrase", text)
        r.setdefault("assumption", "")
        r.setdefault("query", text)
    from core.contracts import SHAPES

    shape = out.get("shape") if out.get("shape") in SHAPES else shape_guess
    return {
        "shape": shape,
        "readings": rs,
        "taken": out.get("taken") if out.get("taken") in ROLES else rs[0]["role"],
        "why": out.get("why", ""),
        "flip_condition": out.get("flip_condition", ""),
    }


def _entropy_bits(ps: list[float]) -> float:
    return -sum(p * math.log2(p) for p in ps if p > 0)


def select_reading(readings: list[dict], prior: list[float]) -> tuple[dict, list[float], float]:
    if not readings:
        raise ValueError("no readings")
    weights = []
    for r in readings:
        role = r.get("role")
        w_prior = prior[ROLES.index(role)] if role in ROLES and ROLES.index(role) < len(prior) else 1.0 / len(ROLES)
        weights.append(max(0.0, float(w_prior)) * max(0.0, float(r.get("p") or 0.0)))
    total = sum(weights)
    post = [w / total for w in weights] if total > 0 else [1.0 / len(readings)] * len(readings)
    i = max(range(len(post)), key=lambda j: post[j])
    return readings[i], post, _entropy_bits(post)


def _strip_tokens(text: str, ok: set[int]) -> str:
    def repl(m: re.Match) -> str:
        return m.group(0) if int(m.group(1)) in ok else ""

    return _CITE_RE.sub(repl, text or "")


def strip_unresolved(result: dict, n_docs: int) -> tuple[dict, int, int]:
    res = dict(result)
    ok = set(range(1, n_docs + 1))
    resolved = total = 0
    claims = []
    for c in res.get("claims") or []:
        c = dict(c)
        ids = []
        for n in c.get("chunk_ids") or []:
            total += 1
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if n in ok:
                resolved += 1
                ids.append(n)
        c["chunk_ids"] = ids
        if "text" in c:
            c["text"] = _strip_tokens(c["text"], ok)
        claims.append(c)
    res["claims"] = claims
    res["contradictions"] = [
        d for d in (res.get("contradictions") or []) if d.get("chunk_a") in ok and d.get("chunk_b") in ok
    ]
    for key in ("screen", "spoken"):
        if key in res:
            res[key] = _strip_tokens(res[key], ok)
    return res, resolved, total


def _clean_spoken(text: str) -> str:
    t = _ANY_BRACKET_RE.sub("", text or "")
    t = t.replace("[", "").replace("]", "")
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def spoken_tier(result: dict, taken: dict) -> str:
    cap = int(CONFIG["spoken_max_words"])
    para = _clean_spoken(str(taken.get("paraphrase") or "")).rstrip(".!? ")
    suffix = f"I read that as {para}."
    body = _SUFFIX_RE.sub("", _clean_spoken(str(result.get("spoken") or "")))
    budget = cap - len(suffix.split())
    if budget <= 0:
        return " ".join(suffix.split()[:cap])
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    kept: list[str] = []
    used = 0
    for s in sentences:
        n = len(s.split())
        if used + n > budget:
            break
        kept.append(s)
        used += n
    if not kept and sentences:
        words = sentences[0].split()[:budget]
        kept = [" ".join(words).rstrip(",;:") + "."]
    out = " ".join(kept)
    if out and out[-1] not in ".!?":
        out += "."
    return f"{out} {suffix}".strip()


def _titles_from_context(context: str) -> str:
    titles = []
    for line in (context or "").splitlines():
        m = re.match(r"\s*\[Document (\d+) \(Source: (.+?)\)\]", line) or re.match(r"\s*\[(\d+)\]\s*(.+)", line)
        if m:
            titles.append(f"[{m.group(1)}] {m.group(2).strip()}")
    return "\n".join(titles)


async def synthesize(text: str, context: str, slot_text: str) -> dict:
    from core import growth, llm

    err = _llm_error()
    system = growth.render_prompt(slot_text, context, text)  # render_prompt already appends CONTRACT
    try:
        out = await _call(llm.call_json, system, text, SYNTHESIS_SCHEMA)
        if not isinstance(out, dict):
            raise TypeError("synthesis was not an object")
    except (err, TypeError):
        return {
            "spoken": FALLBACK_SPOKEN,
            "screen": _titles_from_context(context),
            "claims": [],
            "contradictions": [],
            "gaps": [],
            "confidence": "low",
        }
    for key, default in (("spoken", ""), ("screen", ""), ("claims", []), ("contradictions", []), ("gaps", []), ("confidence", "low")):
        out.setdefault(key, default)
    return out


async def ask(text: str, session_id: str) -> dict:
    from core import db, growth, learn, retrieve

    t0 = time.perf_counter()
    r = await readings(text)
    shape = r["shape"]
    try:
        prior = list(await _call(learn.get_prior, shape))
    except Exception:
        prior = [1.0 / len(ROLES)] * len(ROLES)
    taken, posterior, entropy = select_reading(r["readings"], prior)
    query = taken.get("query") or text

    context, citations, _rows = await _call(retrieve.retrieve, query, k=CONFIG["knn_k"])
    citations = list(citations or [])
    slot = growth.load_slot()
    synth = await synthesize(text, context, slot)
    synth, resolved, total = strip_unresolved(synth, len(citations))
    spoken = spoken_tier(synth, taken)

    by_n = {c.n: c for c in citations}
    chunk_ids: list[str] = []
    for c in synth["claims"]:
        for n in c["chunk_ids"]:
            cit = by_n.get(n)
            if cit and cit.chunk_id not in chunk_ids:
                chunk_ids.append(cit.chunk_id)

    out_readings = [
        {"role": rd.get("role"), "paraphrase": rd.get("paraphrase", ""), "p": rd.get("p", 0.0), "posterior": post}
        for rd, post in zip(r["readings"], posterior)
    ]
    row = {
        "text": text,
        "shape": shape,
        "session_id": session_id,
        "readings": [dict(rd, posterior=post) for rd, post in zip(r["readings"], posterior)],
        "taken": taken.get("role"),
        "split": "demo",
        "ts": db.now(),
        "created_at": db.now(),
    }
    created = await db.q("CREATE question CONTENT $row", {"row": row})
    question_id = str(created[0]["id"]) if created and isinstance(created[0], dict) and created[0].get("id") else ""

    try:
        await learn.record_answer(question_id, shape, taken.get("role"), chunk_ids)
    except Exception as e:  # non-fatal: the answer stands even if the ledger write fails
        import logging
        logging.getLogger("mycelium.reflex").warning("record_answer failed: %s", type(e).__name__)

    return {
        "question_id": question_id,
        "session_id": session_id,
        "shape": shape,
        "readings": out_readings,
        "taken": taken.get("role"),
        "taken_reading": dict(taken),
        "why": r.get("why", ""),
        "flip_condition": r.get("flip_condition", ""),
        "spoken": spoken,
        "screen": synth.get("screen", ""),
        "claims": synth.get("claims", []),
        "contradictions": synth.get("contradictions", []),
        "gaps": synth.get("gaps", []),
        "confidence": synth.get("confidence", "low"),
        "citations": [c.to_dict() for c in citations],
        "citations_resolved": f"{resolved}/{total}",
        "prior": prior,
        "entropy_bits": entropy,
        "slot_version": growth.slot_version(),
        "ms": int((time.perf_counter() - t0) * 1000),
    }
