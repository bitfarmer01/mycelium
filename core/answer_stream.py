"""SSE answer streaming (feature 3): the ask pipeline as an event generator.

Stage-aware twin of ``core.reflex.ask``: it reuses reflex's public stages
(readings, select_reading, synthesize, strip_unresolved, spoken_tier) and
emits one event per stage, so a client renders progress while synthesis is
still running instead of waiting on the full JSON answer.

The synthesis delta source is injectable (``synth``): tests drive a mocked
model stream through it, and workstream C's streaming-JSON renderer
(feature 29) can supply a real incremental source without changing the
event contract. Events, in order: ``start``, ``readings``, ``context``,
``synthesis`` (zero or more), then ``done`` (carrying the full answer
payload) — or ``error`` when the pipeline fails mid-stream, so a demo
never sees a dead connection.

Reflex owns the non-streaming path, so the final-assembly tail is mirrored
here rather than imported (workstream E owns ``reflex.py``; a shared
extract would force a cross-workstream edit). ``test_ask_stream.py`` pins
the ``done`` payload key-for-key against ``reflex.ask`` so the twins
cannot drift silently.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import AsyncIterator
from typing import Any

Event = dict[str, Any]

# The ask answer payload's keys, in the order reflex.ask builds them. Both
# twins must emit exactly this set in `done`.
ANSWER_KEYS = (
    "question_id", "session_id", "shape", "readings", "taken", "taken_reading",
    "why", "flip_condition", "spoken", "screen", "claims", "contradictions",
    "gaps", "confidence", "citations", "citations_resolved", "cached", "usage",
    "prior", "entropy_bits", "slot_version", "ms",
)


async def _call(fn, *args, **kwargs):
    """Await a sibling function that may be sync (off-loop) or async."""
    if inspect.iscoroutinefunction(fn):
        res = await fn(*args, **kwargs)
    else:
        res = await asyncio.to_thread(fn, *args, **kwargs)
    if inspect.isawaitable(res):
        res = await res
    return res


async def default_synth(text: str, context: str, slot_text: str, *, usage: dict | None,
                        session: str | None, taken: dict) -> AsyncIterator[Event]:
    """Default synthesis source: one blocking call, spoken text streamed as sentences.

    Until C's streaming-JSON path (feature 29) lands, deltas are emitted after
    the synthesis call resolves; the event contract is identical either way, so
    the console and tests cannot tell the sources apart.
    """
    from core import reflex

    out = await reflex.synthesize(text, context, slot_text, usage=usage, session=session)
    for sentence in re.split(r"(?<=[.!?])\s+", str(out.get("spoken") or "")):
        if sentence.strip():
            yield {"delta": sentence.strip()}
    yield {"result": out}


async def answer_stream(text: str, session_id: str, *, synth: Any = None) -> AsyncIterator[Event]:
    """Run the ask pipeline, yielding one event per stage (see module docstring)."""
    t0 = time.perf_counter()

    def ms() -> int:
        return int((time.perf_counter() - t0) * 1000)

    try:
        from core import db, growth, learn, reflex, retrieve
        from core.contracts import CONFIG, ROLES

        if synth is None:
            synth = default_synth
        usage: dict = {}

        yield {"type": "start", "session_id": session_id, "ms": ms()}

        r = await reflex.readings(text, usage=usage, session=session_id)
        shape = r["shape"]
        try:
            prior = list(await _call(learn.get_prior, shape))
        except Exception:  # noqa: BLE001  # a missing prior falls back to uniform
            prior = [1.0 / len(ROLES)] * len(ROLES)
        taken, posterior, entropy = reflex.select_reading(r["readings"], prior)
        yield {
            "type": "readings",
            "shape": shape,
            "taken": taken.get("role"),
            "readings": [
                {"role": rd.get("role"), "paraphrase": rd.get("paraphrase", ""), "p": rd.get("p", 0.0), "posterior": post}
                for rd, post in zip(r["readings"], posterior)
            ],
            "why": r.get("why", ""),
            "flip_condition": r.get("flip_condition", ""),
            "prior": prior,
            "entropy_bits": entropy,
            "ms": ms(),
        }

        context, citations, _rows = await _call(retrieve.retrieve, taken.get("query") or text, k=CONFIG["knn_k"])
        citations = list(citations or [])
        yield {"type": "context", "n_chunks": len(citations),
               "citations": [c.to_dict() for c in citations], "ms": ms()}

        slot = growth.load_slot()
        synth_out: dict | None = None
        async for ev in synth(text, context, slot, usage=usage, session=session_id, taken=taken):
            if isinstance(ev.get("delta"), str):
                yield {"type": "synthesis", "delta": ev["delta"], "ms": ms()}
            elif isinstance(ev.get("result"), dict):
                synth_out = ev["result"]
        if synth_out is None:  # a source that streamed deltas but never resolved
            synth_out = {"spoken": "", "screen": "", "claims": [], "contradictions": [],
                         "gaps": [], "confidence": "low"}

        synth_out, resolved, total = reflex.strip_unresolved(synth_out, len(citations))
        spoken = reflex.spoken_tier(synth_out, taken)

        by_n = {c.n: c for c in citations}
        chunk_ids: list[str] = []
        for c in synth_out.get("claims", []):
            for n in c.get("chunk_ids", []):
                cit = by_n.get(n)
                if cit and cit.chunk_id not in chunk_ids:
                    chunk_ids.append(cit.chunk_id)

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
        except Exception as e:  # noqa: BLE001  # non-fatal: the answer stands even if the ledger write fails
            import logging
            logging.getLogger("mycelium.answer_stream").warning("record_answer failed: %s", type(e).__name__)

        try:
            if usage:  # empty only when every model call was a session-cache hit: no tokens, no row
                tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
                await learn.write_ledger(reflex.LedgerRow(
                    target=question_id or "ask", target_kind="question", type="usage",
                    delta=float(tokens), before=0.0, after=float(tokens),
                    cause=f"{usage.get('attempts', 0)} model calls"))
        except Exception as e:  # noqa: BLE001  # non-fatal: the answer stands even if usage accounting fails
            import logging
            logging.getLogger("mycelium.answer_stream").warning("usage ledger write failed: %s", type(e).__name__)

        answer = {
            "question_id": question_id,
            "session_id": session_id,
            "shape": shape,
            "readings": [
                {"role": rd.get("role"), "paraphrase": rd.get("paraphrase", ""), "p": rd.get("p", 0.0), "posterior": post}
                for rd, post in zip(r["readings"], posterior)
            ],
            "taken": taken.get("role"),
            "taken_reading": dict(taken),
            "why": r.get("why", ""),
            "flip_condition": r.get("flip_condition", ""),
            "spoken": spoken,
            "screen": synth_out.get("screen", ""),
            "claims": synth_out.get("claims", []),
            "contradictions": synth_out.get("contradictions", []),
            "gaps": synth_out.get("gaps", []),
            "confidence": synth_out.get("confidence", "low"),
            "citations": [c.to_dict() for c in citations],
            "citations_resolved": f"{resolved}/{total}",
            "cached": bool(synth_out.get("cached")),
            "usage": dict(usage),
            "prior": prior,
            "entropy_bits": entropy,
            "slot_version": growth.slot_version(),
            "ms": ms(),
        }
        yield {"type": "done", "answer": answer, "ms": ms()}
    except asyncio.CancelledError:
        raise  # client went away; shutdown drain owns any in-flight write
    except Exception as e:  # noqa: BLE001  # degrade to an error event; a demo never sees a dead connection
        yield {"type": "error", "detail": str(e)[:300], "ms": ms()}
