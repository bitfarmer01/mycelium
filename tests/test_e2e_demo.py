"""End-to-end demo path (spec, Verification level 3): ask with citations, correct an
answer, watch the prior move, run an exam burst against the test-double provider — plus
every external-service-down path (graceful abstain, speechSynthesis fallback, NumPy
retrieval fallback) and the SurrealDB-4 addendum verification (ledger/learning writes on
the engine that removed type::thing).

Runs against the live stack (SURREAL_URL set, corpus ingested); the model surface is the
fake_llm double, the exam surface the FakeSandboxProvider — the spec's rule that a demo
never depends on a live model credential.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fake_provider import FakeSandboxProvider

from core.contracts import LEDGER_TYPES, ROLES, SHAPES

pytestmark = pytest.mark.integration

Q = "what test runner does voiceai use"
READINGS = {
    "shape": "factual",
    "readings": [
        {
            "role": "literal",
            "paraphrase": "q",
            "assumption": "a",
            "query": "voiceai test runner",
            "p": 0.3,
        },
        {
            "role": "intent",
            "paraphrase": "q",
            "assumption": "a",
            "query": "voiceai test runner",
            "p": 0.4,
        },
        {
            "role": "scope",
            "paraphrase": "q",
            "assumption": "a",
            "query": "voiceai test runner",
            "p": 0.3,
        },
    ],
    "taken": "intent",
    "why": "w",
    "flip_condition": "f",
}
SYNTH = {
    "spoken": "The wiki covers this in the cited document.",
    "screen": "The wiki covers this in the cited document. [1]",
    "claims": [
        {"text": "The wiki covers this in the cited document.", "chunk_ids": [1]}
    ],
    "contradictions": [],
    "gaps": [],
    "confidence": "medium",
}

PACKS = [
    {
        "question_id": f"q{i}",
        "text": f"question {i}",
        "context": f"context {i}",
        "n_docs": 3,
    }
    for i in range(3)
]
CANDIDATES = {
    "suffixes": [
        {"name": f"cand-{i}", "axis": "test", "text": f"Apply rule {i}."}
        for i in range(3)
    ]
}
EXAM_FILES = {
    "candidates.json": CANDIDATES,
    "baseline_runs.json": {"runs": [[0.5, 0.5], [0.6, 0.4]], "runner": "fake"},
    "heldout_packs.json": PACKS,
    "terms.json": {"q0": ["wiki"], "q1": ["wiki"], "q2": ["wiki"]},
}


@pytest.fixture
async def client():
    """The ASGI client, entered once per test — httpx forbids re-entering a closed
    client, so tests use this instance directly instead of their own with-blocks."""
    from app.server import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _ask(client, fake_llm, session):
    fake_llm.push(READINGS, SYNTH)
    r = await client.post("/ask", json={"text": Q, "session_id": session})
    assert r.status_code == 200, r.text
    return r.json()


async def test_demo_path_ask_cite_correct_prior_exam_burst(
    client, fake_llm, monkeypatch
):
    """The demo beats, in order, against the live DB: ask resolves citations → a
    correction moves the corrected role's prior up and writes the ledger → the priors
    API shows it → an exam burst on the test-double provider scores candidates and
    persists history."""
    from core import exam, growth

    # beat 1: ask with citations
    answer = await _ask(client, fake_llm, "e2e-demo-1")
    assert answer["citations"], (
        "ingested corpus must yield citations on the demo question"
    )
    assert answer["citations_resolved"] == "1/1"
    assert answer["spoken"] and "[1]" in answer["screen"]
    qid, shape = answer["question_id"], answer["shape"]
    assert qid and shape in SHAPES

    # beat 2: correct → the prior moves toward the corrected role, ledger records it
    to_role = next(r for r in ROLES if r != answer["taken"])
    r = await client.post("/correct", json={"question_id": qid, "to_role": to_role})
    assert r.status_code == 200, r.text
    out = r.json()
    i = ROLES.index(to_role)
    assert out["prior_after"][i] > out["prior_before"][i], (
        "the corrected role's prior must move up"
    )
    assert len(out.get("ledger_ids") or []) >= 1, "the correction writes ledger rows"

    # beat 3: watch the prior move on the dashboard's data API (feature 31)
    r = await client.get("/learning/priors")
    assert r.status_code == 200, r.text
    rows = r.json() if isinstance(r.json(), list) else r.json().get("priors", [])
    row = next((x for x in rows if x.get("shape") == shape), None)
    assert row and row["prior"][i] == pytest.approx(out["prior_after"][i]), (
        "/learning/priors must serve the moved prior for the question's shape"
    )

    # beat 4: exam burst against the test-double provider (spec open question: no live
    # sandbox credentials), wired exactly like the pool tests
    provider = FakeSandboxProvider()
    monkeypatch.setattr(exam, "_pool_provider", lambda: provider)
    monkeypatch.setattr(exam, "_POOL", None)
    monkeypatch.setattr(growth, "_load_json", lambda name: EXAM_FILES.get(name))
    monkeypatch.setenv("EXAM_POOL_SIZE", "2")

    async def fake_run(slot, packs, label):
        return {
            "runner": "fake",
            "sandbox_id": f"fake-{label}",
            "ms": 1,
            "answers": [
                {"question_id": p["question_id"], "result": None, "ms": 0}
                for p in packs
            ],
        }

    monkeypatch.setattr(
        "core.runner.make_runner",
        lambda: type("F", (), {"run": staticmethod(fake_run)})(),
    )
    r = await client.post("/exam/burst")
    assert r.status_code == 200, r.text
    burst = r.json()
    assert burst["candidates"], "the burst must report candidate rows"
    assert burst["poisons"] is not None, "every burst path returns the poison report"

    r = await client.get("/exam/history")
    assert r.status_code == 200, r.text
    history = r.json() if isinstance(r.json(), list) else r.json().get("candidates", [])
    assert history, "burst results must persist as exam history (feature 41)"


async def test_cli_down_abstains_gracefully_and_records_it(client, fake_llm):
    """Feature 30, end to end: with the model CLI unreachable, /ask still returns HTTP 200
    with a speakable abstain answer, and the ledger records the failure (demo-day rule:
    nothing 500s while a service is down)."""
    from core import db
    from core.llm import CLIDown

    fake_llm.push(CLIDown("simulated: claude binary missing"))
    r = await client.post("/ask", json={"text": Q, "session_id": "e2e-abstain"})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["abstained"] is True and answer["abstain_reason"] == "cli_unreachable"
    assert answer["citations_resolved"] == "0/0" and answer["spoken"]
    qid = answer["question_id"]
    assert qid, "the abstain still seeds its question row"
    led = await db.q(
        "SELECT * FROM ledger WHERE target = $t AND type = 'abstain';", {"t": qid}
    )
    assert led, "the abstain must be recorded in the ledger"


async def test_tts_down_leaves_a_speakable_console(client, monkeypatch):
    """Feature 58's server half: with no ElevenLabs key, /tts degrades to a clean 502 —
    and the console routes playback errors to speechSynthesis with a visible badge
    (static wiring here; the badge itself is dogfooded in the browser evidence)."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    r = await client.get("/tts", params={"text": "hello"})
    assert r.status_code == 502, (
        f"tts without a key must degrade to 502, got {r.status_code}"
    )

    audio = (Path("app/static/js/audio.js")).read_text(encoding="utf-8")
    play = audio[audio.find("/* play audio_url") :]
    assert "browserSpeak" in play, (
        "audio.js must route playback errors to speechSynthesis"
    )
    assert "setFallback(true)" in audio, "the fallback must turn the visible badge on"


async def test_retrieval_falls_back_to_numpy_scan_when_knn_query_fails(
    client, monkeypatch
):
    """Demo-day rule, retrieval half: when the SurrealDB KNN query fails (HNSW trouble,
    engine issue), the numpy cosine scan takes over and citations still resolve."""
    from core import db, retrieve

    seeded = await db.q("SELECT id, embedding FROM chunk LIMIT 1")
    if not seeded:
        pytest.skip("corpus not ingested into the live DB")
    vec = list(map(float, seeded[0]["embedding"]))
    real_q = db.q

    async def broken_knn_q(sql, vars=None):
        if "vector::similarity::cosine" in sql:  # the HNSW/KNN surface is down
            raise RuntimeError("simulated: KNN engine unavailable")
        return await real_q(sql, vars)

    monkeypatch.setattr(
        retrieve, "embed_query", lambda *_a, **_k: vec
    )  # no model load: reuse the stored embedding
    monkeypatch.setattr("core.db.q", broken_knn_q)

    ctx, cites, _top = await retrieve.retrieve(Q, k=4)
    assert ctx.strip(), "the context pack is non-empty on the fallback path too"
    assert cites, "the numpy fallback must still produce citations"
    # The exact-embedding chunk competes under hybrid scoring (cosine + BM25), so
    # rank 1 is not guaranteed — but the fallback must surface it in the top-k.
    assert str(seeded[0]["id"]) in {c.chunk_id for c in cites}, (
        "with the query embedding equal to a stored chunk's, that chunk stays in top-k"
    )


async def test_newest_surrealdb_serves_every_ledger_and_learning_write():
    """Orchestrator addendum: verify ledger/learning writes on the engine family that
    removed type::thing. Three legs:
    1. the booted engine rejects type::thing outright (so the old syntax would be loud);
    2. the learn.py write paths (ledger, lesson, prior UPSERT) succeed on it;
    3. no source file still calls type::thing — a regression lands here, not in a demo."""
    from core import db, learn
    from core.contracts import LedgerRow

    # 1. the engine is past type::thing — core.db.q raises RuntimeError on a statement error
    with pytest.raises(RuntimeError):
        await db.q('RETURN type::thing("ledger", "probe");')

    # 2. the real write paths, against the live engine
    rid = await learn.write_ledger(
        LedgerRow(
            target="e2e-probe",
            target_kind="question",
            type="usage",
            delta=3.0,
            before=0.0,
            after=3.0,
            cause="addendum probe",
        )
    )
    assert rid, "write_ledger must return the created row id"
    back = await db.q(f"SELECT * FROM {rid}")
    assert back and back[0]["cause"] == "addendum probe", (
        "the ledger row must read back"
    )

    lesson_id = await learn.write_lesson(
        "e2e",
        "addendum probe lesson",
        "writes on the new engine",
        "integration-test",
        [],
        [],
        "observation",
    )
    assert lesson_id, "write_lesson must persist"

    prior = await learn.get_prior("factual")
    assert len(prior) == len(ROLES) and abs(sum(prior) - 1.0) < 1e-6, (
        "get_prior must serve a normalized prior"
    )

    # 3. no call site anywhere in the shipped surfaces still uses type::thing(
    # (this test's own probe above deliberately quotes the legacy call, so the
    # scan covers core/, app/, scripts/, and infra/ — not the tests)
    root = Path(__file__).resolve().parents[1]
    hits = [
        str(p)
        for d in ("core", "app", "scripts", "infra")
        for p in (root / d).rglob("*")
        if p.suffix in {".py", ".surql"}
        and p.is_file()
        and "type::thing(" in p.read_text(errors="ignore")
    ]
    assert not hits, f"type::thing call sites remain: {hits}"
    assert (
        "abstain" in LEDGER_TYPES and "usage" in LEDGER_TYPES
    )  # the write paths above are contract types
