"""Eval-split regression guard machinery (scripts/run_baseline.py --retrieval-only): pure helpers, no model, no DB."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from core import retrieve as r

_spec = importlib.util.spec_from_file_location("run_baseline", Path(__file__).resolve().parents[1] / "scripts" / "run_baseline.py")
rb = importlib.util.module_from_spec(_spec)
sys.modules["run_baseline"] = rb
_spec.loader.exec_module(rb)


def _rows():
    keyword = {"id": "k", "document": "HNSW index: set efc to 40 before bulk insert. " * 5,
               "title": "K", "source_link": "", "alpha": 1.0, "beta": 1.0, "cosine": 0.55}
    generic = {"id": "g", "document": "General platform overview of services and defaults. " * 5,
               "title": "G", "source_link": "", "alpha": 3.0, "beta": 1.0, "cosine": 0.85}
    return [generic, keyword]


def test_eval_rankings_keyword_probe_rescues_right_chunk():
    baseline, hybrid = rb.eval_rankings(_rows(), "hnsw efc", floor=0.5, weight=0.35)
    assert baseline[0]["id"] == "g"  # cosine-only buries the keyword chunk
    assert hybrid[0]["id"] == "k"  # hybrid ranks the exact-term chunk first


def test_eval_rankings_does_not_mutate_input():
    rows = _rows()
    rb.eval_rankings(rows, "hnsw efc", floor=0.5, weight=0.35)
    assert all("score" not in row and "bm25" not in row for row in rows)


def test_is_relevant_and_hit_at_k():
    ranked = [{"document": "Nothing useful here."}, {"document": "HNSW efc tuning notes."}]
    assert rb.is_relevant(ranked[0], ["hnsw"]) is False
    assert rb.is_relevant(ranked[1], ["HNSW"]) is True  # case-insensitive
    assert rb.hit_at_k(ranked, ["hnsw"], 1) == 0
    assert rb.hit_at_k(ranked, ["hnsw"], 2) == 1
    assert rb.hit_at_k([], ["hnsw"], 3) == 0


def test_hybrid_guard_via_eval_rankings_on_real_weight():
    """Regression guard: on keyword-only queries hybrid must never lose the chunk cosine-only finds."""
    rows = _rows()
    terms = ["hnsw"]
    baseline, hybrid = rb.eval_rankings(rows, " ".join(terms), floor=0.5, weight=r.BM25_WEIGHT)
    assert rb.hit_at_k(hybrid, terms, 1) >= rb.hit_at_k(baseline, terms, 1)
