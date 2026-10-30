"""Feature 3: SSE streaming for /ask.

Covers the three acceptance surfaces: an SSE client receives incremental
chunks from a mocked model stream plus a final done event; the non-streaming
/ask path is unchanged; and the first event reaches the client long before the
full answer (<1.5 s). Also pins the done payload key-for-key against
reflex.ask so the streaming twin cannot drift from the non-streaming one.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import types

import httpx
import pytest

from core.contracts import Citation


def _stub(monkeypatch, name: str, **funcs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in funcs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def parse_sse(buf: str) -> list[tuple[str, dict]]:
    """Parse complete SSE frames into (event, data) pairs."""
    out = []
    for frame in buf.split("\n\n"):
        if not frame.strip():
            continue
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        out.append((ev, json.loads(data) if data else {}))
    return out


class SseCollector:
    """Consumes the stream incrementally, recording each frame's arrival."""

    def __init__(self):
        self.frames: list[tuple[str, dict]] = []
        self.arrival: list[float] = []

    async def read(self, client, **kw):
        t0 = time.monotonic()
        buf = ""
        async with client.stream("POST", "/ask/stream", json=kw) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            async for chunk in r.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    parsed = parse_sse(frame + "\n\n")
                    if parsed:
                        self.frames.extend(parsed)
                        self.arrival.append(time.monotonic() - t0)

    def types(self) -> list[str]:
        return [ev for ev, _ in self.frames]

    def first(self, ev_type: str) -> dict:
        return next(d for e, d in self.frames if e == ev_type)

    def all(self, ev_type: str) -> list[dict]:
        return [d for e, d in self.frames if e == ev_type]


# Fakes shared by the real-pipeline tests (mirrors test_reflex's full-ask setup).
READINGS = {
    "shape": "who-owns",
    "readings": [
        {"role": "literal", "paraphrase": "who owns the budget", "assumption": "", "query": "budget owner", "p": 2},
        {"role": "intent", "paraphrase": "who approves spend", "assumption": "", "query": "approve spend", "p": 1},
        {"role": "scope", "paraphrase": "which team budget", "assumption": "", "query": "team budget", "p": 1},
    ],
    "taken": "literal",
    "why": "most direct",
    "flip_condition": "if asking about approvals",
}
SYNTHESIS = {
    "spoken": "Finance owns the budget [1]. Approvals go to the CFO [2].",
    "screen": "Finance owns the budget [1]. Approvals go to the CFO [2].",
    "claims": [
        {"text": "Finance owns the budget", "chunk_ids": [1]},
        {"text": "Approvals go to the CFO", "chunk_ids": [2]},
    ],
    "contradictions": [],
    "gaps": [],
    "confidence": "high",
}
CITES = [
    Citation(1, "chunk:a", "Budget", "http://x/a", "Finance owns", 0.9),
    Citation(2, "chunk:b", "Approvals", "http://x/b", "CFO approves", 0.8),
]


def _fake_pipeline(monkeypatch, fake_llm, fake_db) -> None:
    import core.growth
    import core.learn
    import core.retrieve

    fake_llm.push(dict(READINGS), dict(SYNTHESIS))

    def fake_retrieve(text, k=8, reading=None):
        return "[1] Budget\n[2] Approvals", list(CITES), []

    async def fake_prior(shape):
        return [0.5, 0.25, 0.25]

    async def fake_record(question_id, shape, taken_role, chunk_ids):
        pass

    async def fake_ledger(row):
        pass

    monkeypatch.setattr(core.retrieve, "retrieve", fake_retrieve, raising=False)
    monkeypatch.setattr(core.learn, "get_prior", fake_prior, raising=False)
    monkeypatch.setattr(core.learn, "record_answer", fake_record, raising=False)
    monkeypatch.setattr(core.learn, "write_ledger", fake_ledger, raising=False)
    monkeypatch.setattr(core.growth, "load_slot", lambda: "SLOT", raising=False)
    monkeypatch.setattr(core.growth, "render_prompt", lambda s, c, q: f"{s}|{c}|{q}|CONTRACT", raising=False)
    monkeypatch.setattr(core.growth, "slot_version", lambda: "1.0", raising=False)
    fake_db.rows["CREATE"] = [{"id": "question:abc"}]


async def test_stream_receives_mocked_chunks_then_done(monkeypatch, client, fake_llm, fake_db):
    """Acceptance: incremental chunks from a mocked model stream + a final done event."""
    import core.answer_stream

    _fake_pipeline(monkeypatch, fake_llm, fake_db)  # stages other than synthesis

    async def mocked_model_stream(text, context, slot_text, *, usage, session, taken):
        for delta in ("Finance owns the budget.", "Approvals go to the CFO."):
            await asyncio.sleep(0.05)  # model latency between tokens
            yield {"delta": delta}
        yield {"result": dict(SYNTHESIS)}

    monkeypatch.setattr(core.answer_stream, "default_synth", mocked_model_stream)
    collector = SseCollector()
    await collector.read(client, text="who owns the budget", session_id="s1")

    assert collector.types()[0] == "start"
    assert collector.types()[-1] == "done"
    # Each mocked delta arrived as its own synthesis event, in order.
    assert [d["delta"] for d in collector.all("synthesis")] == [
        "Finance owns the budget.", "Approvals go to the CFO.",
    ]
    # Incremental: the first chunk arrived while the stream was still open
    # (before done), not batched with it.
    types_seen = collector.types()
    assert types_seen.index("synthesis") < types_seen.index("done")
    done = collector.first("done")
    assert done["answer"]["question_id"] == "question:abc"
    assert done["answer"]["citations_resolved"] == "2/2"
    assert done["answer"]["taken"] == "literal"
    assert done["answer"]["session_id"] == "s1"


async def test_stream_event_order_stages(client, fake_llm, fake_db, monkeypatch):
    _fake_pipeline(monkeypatch, fake_llm, fake_db)
    collector = SseCollector()
    await collector.read(client, text="who owns the budget", session_id="s2")

    types_seen = collector.types()
    # start, readings, context precede synthesis deltas; done is last; no error.
    assert types_seen[0] == "start"
    assert types_seen[1] == "readings"
    assert types_seen[2] == "context"
    assert "error" not in types_seen
    assert types_seen[-1] == "done"
    readings = collector.first("readings")
    assert readings["taken"] == "literal" and readings["shape"] == "who-owns"
    assert len(readings["readings"]) == 3
    assert collector.first("context")["n_chunks"] == 2
    # The done answer's spoken text is the citation-cleaned spoken tier.
    assert collector.first("done")["answer"]["spoken"].endswith("I read that as who owns the budget.")


async def test_done_payload_matches_ask_contract(client, fake_llm, fake_db, monkeypatch):
    """Drift guard: the streaming twin emits exactly reflex.ask's answer keys."""
    import core.answer_stream
    import core.reflex

    _fake_pipeline(monkeypatch, fake_llm, fake_db)
    collector = SseCollector()
    await collector.read(client, text="who owns the budget", session_id="s3")
    assert collector.types()[-1] == "done"
    stream_keys = set(collector.first("done")["answer"].keys())

    fake_llm.push(dict(READINGS), dict(SYNTHESIS))  # fresh queue for the non-streaming twin
    out_ask = await core.reflex.ask("who owns the budget", "s3")
    assert stream_keys == set(out_ask.keys())
    assert stream_keys == set(core.answer_stream.ANSWER_KEYS)


async def test_first_event_arrives_before_answer_completes(client, fake_llm, fake_db, monkeypatch):
    """Acceptance timing: first frame well under 1.5 s while synthesis is still slow."""
    import core.answer_stream

    _fake_pipeline(monkeypatch, fake_llm, fake_db)  # stages other than synthesis

    async def slow_synth(text, context, slot_text, *, usage, session, taken):
        await asyncio.sleep(1.2)  # a realistic synthesis call
        yield {"delta": "late"}
        yield {"result": dict(SYNTHESIS)}

    monkeypatch.setattr(core.answer_stream, "default_synth", slow_synth)
    collector = SseCollector()
    await collector.read(client, text="who owns the budget", session_id="s4")

    start_idx = collector.types().index("start")
    assert collector.arrival[start_idx] < 1.5
    # start landed while synthesis was still running, not after it.
    first_synth_idx = collector.types().index("synthesis")
    assert collector.arrival[start_idx] < collector.arrival[first_synth_idx]


async def test_non_streaming_ask_unchanged(client, fake_llm, fake_db, monkeypatch):
    """Acceptance: without SSE the original full-JSON /ask behavior is untouched."""
    _fake_pipeline(monkeypatch, fake_llm, fake_db)
    async with client as c:
        r = await c.post("/ask", json={"text": "who owns the budget", "session_id": "s5"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "text/event-stream" not in r.headers.get("content-type", "")
    body = r.json()
    assert body["question_id"] == "question:abc"
    assert body["citations_resolved"] == "2/2"
    assert body["audio_url"].startswith("/tts?text=")


async def test_stream_route_framing_with_stubbed_module(monkeypatch, client):
    """Route contract: SSE framing survives whatever generator the core exports."""

    async def gen(text, session_id):
        assert (text, session_id) == ("q", "sid-9")
        yield {"type": "start", "session_id": session_id}
        yield {"type": "synthesis", "delta": "he"}
        yield {"type": "done", "answer": {"spoken": "hi"}}

    _stub(monkeypatch, "core.answer_stream", answer_stream=gen)
    async with client as c:
        r = await c.post("/ask/stream", json={"text": "q", "session_id": "sid-9"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert [ev for ev, _ in parse_sse(r.text)] == ["start", "synthesis", "done"]
    assert next(d for e, d in parse_sse(r.text) if e == "synthesis")["delta"] == "he"


async def test_stream_defaults_session_id(monkeypatch, client):
    seen = {}

    async def gen(text, session_id):
        seen["sid"] = session_id
        yield {"type": "done", "answer": {}}

    _stub(monkeypatch, "core.answer_stream", answer_stream=gen)
    async with client as c:
        r = await c.post("/ask/stream", json={"text": "q"})
    assert r.status_code == 200
    assert seen["sid"]


async def test_demo_page_served(client):
    async with client as c:
        r = await c.get("/ask/stream/demo")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "/ask/stream" in r.text
