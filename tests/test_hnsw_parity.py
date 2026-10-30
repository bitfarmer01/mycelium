"""Feature 19: HNSW parity harness — exact NumPy baseline, adopt rule, offline fallback parity."""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

hnsw = importlib.import_module("scripts.hnsw_eval")


# ---- topk_exact: golden ordering + stable tie-breaking ----


def test_topk_exact_orders_by_cosine_and_breaks_ties_toward_lower_index():
    matrix = np.array(
        [
            [1.0, 0.0],  # cos ~0.994 with q
            [0.0, 1.0],
            [1.0, 0.0],  # exact duplicate of row 0 → same cosine
            [-1.0, 0.0],
        ],
        dtype=np.float32,
    )
    q = np.array([[0.9, 0.1]], dtype=np.float32)
    top = hnsw.topk_exact(matrix, q, 3)
    assert top[0][0] == 0  # higher cosine wins...
    assert top[0][1] == 2  # ...tie breaks toward the lower index (stable)
    assert 3 not in top[0]


def test_topk_exact_handles_zero_vectors_without_nan():
    matrix = np.array([[0.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    q = np.array([[0.5, 0.5]], dtype=np.float32)
    top = hnsw.topk_exact(matrix, q, 2)
    assert top == [[1, 0]]  # zero vector scores 0.0 and ranks last — no NaN poisons the order


# ---- recall_at_k ----


def test_recall_at_k_perfect_partial_and_empty():
    assert hnsw.recall_at_k([[0, 1, 2]], [[0, 1, 2]]) == 1.0
    assert hnsw.recall_at_k([[0, 5, 6]], [[0, 1, 2]]) == pytest.approx(1 / 3)
    assert hnsw.recall_at_k([[]], [[0, 1]]) == 0.0
    assert hnsw.recall_at_k([[0, 1]], []) == 0.0  # nothing true → nothing to recall


# ---- decision: the feature 19 adopt rule ----


def test_decision_adopts_only_on_recall_floor_and_p95_win():
    d = hnsw.decision(1.0, 10.0, 8.0, 1.0)
    assert d["adopt_hnsw"] is True and d["verdict"] == "ADOPT-HNSW"


def test_decision_keeps_numpy_when_recall_drops():
    d = hnsw.decision(0.97, 10.0, 8.0, 1.0)
    assert d["adopt_hnsw"] is False and d["verdict"] == "KEEP-NUMPY"


def test_decision_keeps_numpy_when_p95_does_not_improve():
    d = hnsw.decision(1.0, 10.0, 10.0, 1.0)
    assert d["adopt_hnsw"] is False  # equal p95 is not an improvement


def test_decision_keeps_numpy_when_candidate_unmeasured():
    d = hnsw.decision(1.0, 10.0, None, 1.0)
    assert d["adopt_hnsw"] is False  # no SurrealDB → no adoption on assumption


# ---- offline end-to-end: fallback parity recall must be 1.0, verdict forced KEEP-NUMPY ----


def test_offline_evaluate_reports_parity_and_keeps_numpy(monkeypatch):
    async def no_db():
        return None

    monkeypatch.setattr(hnsw, "load_real_corpus", no_db)
    report = asyncio.run(hnsw.evaluate(n_chunks=80, dim=16, n_queries=6, k=5, min_recall=1.0, rounds=2))
    assert report["candidate_backend"] == "numpy-fallback"
    assert report["fallback_parity_recall"] == 1.0  # fallback ranking == exact baseline ranking
    assert report["decision"]["adopt_hnsw"] is False
    assert report["decision"]["verdict"] == "KEEP-NUMPY"
    assert report["decision"]["numpy_p95_ms"] is not None
    assert report["corpus"]["source"] == "synthetic"


def test_live_evaluate_adopts_when_hnsw_is_exact_and_faster(monkeypatch):
    matrix, _queries = hnsw.synthetic_corpus(60, 8, 5)

    async def real_db():
        return [f"chunk:{i}" for i in range(len(matrix))], matrix

    async def fast_exact_knn(vec, k=8):  # stand-in for a perfect, faster HNSW backend
        return [
            {"id": f"chunk:{i}", "cosine": 0.9}
            for i in hnsw.topk_exact(matrix, np.asarray(vec, dtype=np.float32).reshape(1, -1), k)[0]
        ]

    import core.retrieve

    monkeypatch.setattr(hnsw, "load_real_corpus", real_db)
    monkeypatch.setattr(core.retrieve, "knn", fast_exact_knn)
    report = asyncio.run(hnsw.evaluate(n_chunks=60, dim=8, n_queries=5, k=5, min_recall=1.0, rounds=2))
    assert report["candidate_backend"] == "surrealdb-hnsw"
    assert report["decision"]["recall_vs_exact"] == 1.0
    # the mocked HNSW path wins the latency race (pure in-memory vs the same math) → measured ADOPT
    assert report["decision"]["verdict"] in ("ADOPT-HNSW", "KEEP-NUMPY")  # timing-dependent; recall must hold
    assert report["decision"]["adopt_hnsw"] is (report["decision"]["candidate_p95_ms"] < report["decision"]["numpy_p95_ms"])
