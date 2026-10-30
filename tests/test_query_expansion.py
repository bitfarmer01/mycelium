"""Feature 15: query expansion from the taken reading's role (core.retrieve)."""
from __future__ import annotations

import asyncio

import numpy as np

from core import retrieve as r

DIM = 64


def _bow(text: str) -> list[float]:
    """Deterministic bag-of-words stand-in for the bge embedder: one dim per token bucket."""
    v = np.zeros(DIM)
    for tok in r.tokenize(text):
        v[hash(tok) % DIM] += 1.0
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


# ---- expansion composition ----


def test_intent_reading_appends_assumption():
    q = r.expand_query("billing", {"role": "intent", "assumption": "who approves invoice payments"})
    assert q == "billing who approves invoice payments"


def test_scope_reading_appends_paraphrase():
    q = r.expand_query("deploy", {"role": "scope", "paraphrase": "staging deploys only"})
    assert q == "deploy staging deploys only"


def test_literal_reading_leaves_query_alone():
    reading = {"role": "literal", "paraphrase": "billing", "assumption": "ignored for literal", "query": "billing"}
    assert r.expand_query("billing", reading) == "billing"


def test_no_reading_or_empty_extra_is_unchanged():
    assert r.expand_query("billing", None) == "billing"
    assert r.expand_query("billing", {}) == "billing"
    assert r.expand_query("billing", {"role": "intent", "assumption": ""}) == "billing"
    assert r.expand_query("billing", {"role": "intent", "assumption": "billing"}) == "billing"


# ---- acceptance: intent-matched chunk reaches top-3 when the intent reading is taken ----


def _corpus():
    return [
        {"id": "chunk:lit", "document": "billing dashboard shows monthly totals billing", "title": "Lit",
         "source_link": "", "alpha": 1.0, "beta": 1.0},
        {"id": "chunk:f1", "document": "billing owners meeting notes billing archive", "title": "F1",
         "source_link": "", "alpha": 1.0, "beta": 1.0},
        {"id": "chunk:f2", "document": "billing glossary definitions billing references", "title": "F2",
         "source_link": "", "alpha": 1.0, "beta": 1.0},
        {"id": "chunk:intent", "document": "the finance lead approves invoice payments reimbursements",
         "title": "Intent", "source_link": "", "alpha": 1.0, "beta": 1.0},
    ]


def _fake_corpus_db(fake_db):
    """Serve the knn full scan and the de-dup embedding fetch from the same in-memory corpus."""
    def handler(sql, vars=None):
        if "IN $ids" in sql:
            return [{"id": c["id"], "embedding": _bow(c["document"])} for c in _corpus()
                    if c["id"] in (vars or {}).get("ids", [])]
        return [dict(c, embedding=_bow(c["document"])) for c in _corpus()]

    fake_db.rows["FROM chunk"] = handler


def test_intent_taken_puts_intent_chunk_in_top3(fake_db, monkeypatch):
    monkeypatch.setattr(r, "embed_query", _bow)
    _fake_corpus_db(fake_db)

    intent = {"role": "intent", "paraphrase": "billing", "assumption": "approves invoice payments reimbursements",
              "query": "billing", "p": 1.0}
    _ctx, cites, _rows = asyncio.run(r.retrieve("billing", k=3, reading=intent))
    top_ids = [c.chunk_id for c in cites]
    assert "chunk:intent" in top_ids

    # and the unexpanded query misses it: cosine 0 and no BM25 overlap leaves it ranked last
    _ctx, cites_plain, _rows = asyncio.run(r.retrieve("billing", k=3))
    assert "chunk:intent" not in [c.chunk_id for c in cites_plain]
