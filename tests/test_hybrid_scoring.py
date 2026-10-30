"""Feature 14: hybrid cosine+BM25 rerank (core.retrieve)."""
from __future__ import annotations

import pytest

from core import retrieve as r


def _row(i, document, cos=0.9, alpha=1.0, beta=1.0):
    return {
        "id": f"chunk:c{i}",
        "document": document,
        "title": f"T{i}",
        "source_link": "",
        "alpha": alpha,
        "beta": beta,
        "cosine": cos,
    }


# ---- feature 14: BM25 ----


def test_tokenize_keeps_product_terms_drops_function_words():
    assert r.tokenize("How do I tune HNSW efc in SurrealDB?") == ["tune", "hnsw", "efc", "surrealdb"]
    assert r.tokenize("The -dash_2 token") == ["dash", "2", "token"]


def test_bm25_ranks_exact_term_match_first():
    query = "HNSW"
    docs = [
        "The wiki home page describes the project and its goals at length. " * 5,
        "HNSW index tuning: set efc before bulk insert; handoff notes cover it. " * 5,
        "Unrelated paragraph about lunch menus and office furniture. " * 5,
    ]
    scores = r.bm25_scores(query, docs)
    assert scores.index(max(scores)) == 1
    assert scores[0] == 0.0 and scores[2] == 0.0


def test_bm25_no_overlap_or_empty_query_gives_zeros():
    docs = ["alpha beta", "gamma delta"]
    assert r.bm25_scores("epsilon zeta", docs) == [0.0, 0.0]
    assert r.bm25_scores("", docs) == [0.0, 0.0]
    assert r.bm25_scores("anything", []) == []


# ---- feature 14: hybrid rerank ----


def test_hybrid_rescues_keyword_chunk_cosine_buries():
    keyword = _row(1, "HNSW index: set efc to 40 before bulk inserting vectors. " * 10, cos=0.55)
    generic = _row(2, "General platform overview of services and defaults. " * 10, cos=0.85, alpha=3, beta=1)
    out = r.hybrid_rerank([generic, keyword], "HNSW efc")
    assert out[0]["id"] == "chunk:c1"  # cosine alone had generic first (0.85 fit-weighted vs 0.55)
    assert out[0]["bm25_norm"] == pytest.approx(1.0)
    assert "bm25" in out[0] and "score" in out[0]


def test_hybrid_preserves_order_when_bm25_all_zero():
    a = _row(1, "nothing here", cos=0.9)
    b = _row(2, "also unrelated text", cos=0.7)
    out = r.hybrid_rerank([b, a], "zzz qqq")
    assert [row["id"] for row in out] == ["chunk:c1", "chunk:c2"]
    assert all(row["bm25_norm"] == 0.0 for row in out)


def test_hybrid_weight_zero_matches_plain_rerank_order():
    rows_a = [_row(1, "HNSW efc doc", cos=0.6), _row(2, "other text", cos=0.8)]
    rows_b = [dict(x) for x in rows_a]
    plain = r.rerank([dict(x) for x in rows_a], floor=0.5)
    zero_w = r.hybrid_rerank(rows_b, "HNSW efc", weight=0.0)
    assert [x["id"] for x in plain] == [x["id"] for x in zero_w]


def test_hybrid_empty_rows():
    assert r.hybrid_rerank([], "query") == []
