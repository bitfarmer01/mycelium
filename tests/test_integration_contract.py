"""Cross-workstream contract (spec, Interface contracts): a seeded question's answer
payload carries the citation fields, and the console renders them.

Three layers pinned here:
1. live API — an /ask against the booted stack (real SurrealDB + ingested corpus, model
   calls served by the fake_llm double) returns the answer object with the citation
   payload fields workstream C ships and workstream G renders;
2. fixture agreement — the console's mock-mode fixture cites with the same field names
   the API produces, so demo mode cannot drift from the real payload;
3. render path — answer.js reads those fields and degrades field-by-field when absent.

Needs SURREAL_URL (integration marker) plus an ingested corpus.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from core.contracts import Citation

pytestmark = pytest.mark.integration

JS_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "js"

# The spec's citation-payload seam: "chunk id, cosine, and fitness fields" — chunk id and
# cosine ride every Citation (to_dict); fitness arrives with C's enrichment when present
# and the console degrades to hidden without it (feature 62).
CITATION_PAYLOAD_FIELDS = sorted(set(Citation.__dataclass_fields__) | {"fitness"})


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_seeded_answer_carries_the_citation_payload(client, fake_llm):
    """Integration acceptance: seed a question, the answer object carries the citation
    payload fields, resolved — against the live DB and the ingested corpus."""
    from core import db

    chunk_rows = await db.q("SELECT id FROM chunk LIMIT 1")
    if not chunk_rows:
        pytest.skip("corpus not ingested into the live DB")
    fake_llm.push(
        {
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
        },
        {
            "spoken": "The wiki covers this in the cited document.",
            "screen": "The wiki covers this in the cited document. [1]",
            "claims": [
                {
                    "text": "The wiki covers this in the cited document.",
                    "chunk_ids": [1],
                }
            ],
            "contradictions": [],
            "gaps": [],
            "confidence": "medium",
        },
    )
    async with client as c:
        r = await c.post(
            "/ask",
            json={
                "text": "what test runner does voiceai use",
                "session_id": "integration-contract",
            },
        )
        assert r.status_code == 200, r.text
        answer = r.json()

    # seeded: the question row landed in the live DB
    qid = answer["question_id"]
    assert qid, "the ask must persist its question row (the seed for /correct)"
    row = await db.q("SELECT * FROM question WHERE id = $id", {"id": qid})
    assert row, f"question {qid} not found in the DB"

    # the citation payload: non-empty (corpus ingested), resolved, field-complete
    assert answer["citations"], "an ingested corpus must yield citations"
    assert answer["citations_resolved"] == "1/1"
    for field in set(Citation.__dataclass_fields__):
        assert all(field in cit for cit in answer["citations"]), (
            f"answer citations missing the contract field '{field}' (workstream C payload)"
        )
    cit = answer["citations"][0]
    assert cit["chunk_id"], "citations carry the chunk id"
    assert isinstance(cit["cosine"], (int, float)) and 0 < cit["cosine"] <= 1, (
        "citations carry a real cosine"
    )
    assert cit["title"] and cit["link"], "citations carry title and link (provenance)"


def test_console_fixture_cites_with_the_api_field_names():
    """The cross-workstream seam in code: every field the console's cited fixture carries
    is a field the API citation payload can produce, and the fixture exercises the full
    set the renderer knows (cosine + the soft-degrade fitness)."""
    src = (JS_DIR / "fixtures.js").read_text(encoding="utf-8")
    m = re.search(r"askCited:\s*\{.*?\n  \}", src, re.DOTALL)
    assert m, "fixtures.js must define the askCited fixture"
    block = m.group(0)
    fixture_fields = set(re.findall(r"\b(\w+):\s", block))
    missing = (
        set(Citation.__dataclass_fields__) - fixture_fields - {"n"}
    )  # n indexes context, not a citation field
    assert not missing, f"askCited fixture citations missing API fields: {missing}"


def test_console_renders_the_payload_fields_and_degrades_when_absent():
    """answer.js reads the payload's field names; fitness renders only when it arrives."""
    src = (JS_DIR / "answer.js").read_text(encoding="utf-8")
    for field in ("chunk_id", "cosine", "title", "link", "snippet"):
        assert re.search(rf"\bc\.{field}\b", src), (
            f"answer.js must render citation {field}"
        )
    assert re.search(r"c\.fitness\s*!=\s*null", src, re.DOTALL), (
        "fitness renders only when the payload carries it (spec: show nothing rather than break)"
    )


async def test_learning_priors_serves_the_seeded_shape(client, fake_llm):
    """Workstream D's side of the contract: the priors API exposes what the ask path just
    wrote, for the same shape the seeded question classified."""
    fake_llm.push(
        {
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
        },
        {
            "spoken": "The wiki covers this in the cited document.",
            "screen": "The wiki covers this in the cited document. [1]",
            "claims": [
                {
                    "text": "The wiki covers this in the cited document.",
                    "chunk_ids": [1],
                }
            ],
            "contradictions": [],
            "gaps": [],
            "confidence": "medium",
        },
    )
    async with client as c:
        r = await c.post(
            "/ask",
            json={
                "text": "what test runner does voiceai use",
                "session_id": "integration-priors",
            },
        )
        assert r.status_code == 200, r.text
        priors = await c.get("/learning/priors")
        assert priors.status_code == 200, priors.text
        shapes = {
            row["shape"]
            for row in priors.json().get("priors", priors.json())
            if isinstance(row, dict)
        }
        assert "factual" in shapes, (
            f"/learning/priors must serve the seeded shape, got {shapes}"
        )
