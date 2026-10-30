"""HNSW evaluation harness (feature 19): SurrealDB HNSW candidates vs exact NumPy top-k.

The shipped retrieval path (core.retrieve.knn) already prefers the SurrealDB HNSW KNN
query and falls back to an exact NumPy scan. This harness measures both sides of the
spec's adopt rule — recall >= NumPy baseline AND p95 latency improves — and records the
decision to data/hnsw_eval.json.

Two modes:

- Live (SurrealDB reachable): loads the real chunk corpus from the DB, runs the real
  knn() HNSW path per query, and scores recall and p95 against the exact NumPy
  baseline over the same matrix. The verdict is a real ADOPT-HNSW / KEEP-NUMPY.
- Offline (no SurrealDB): builds a deterministic synthetic corpus and verifies the
  NumPy fallback's ranking math against the exact baseline (parity recall must be
  1.0). HNSW itself is unmeasured, so the verdict is forced to KEEP-NUMPY — adoption
  needs a live measured run, never an assumption.

Pure functions (topk_exact, recall_at_k, decision) are unit-tested in
tests/test_hnsw_parity.py; the offline end-to-end runs in CI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.retrieve import pctl

REPORT_PATH = ROOT / "data" / "hnsw_eval.json"


def topk_exact(matrix: np.ndarray, queries: np.ndarray, k: int) -> list[list[int]]:
    """Exact NumPy cosine top-k; ties break toward lower index (stable sort)."""
    norms = np.clip(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12, None)
    qnorms = np.clip(np.linalg.norm(queries, axis=1, keepdims=True), 1e-12, None)
    sims = (queries / qnorms) @ (matrix / norms).T
    return [np.argsort(-row, kind="stable")[:k].tolist() for row in sims]


def fallback_scan(matrix: np.ndarray, queries: np.ndarray, k: int) -> list[list[int]]:
    """Replica of the NumPy branch in core.retrieve.knn (denominator = |row|*|q| cosine).

    Kept in lockstep with that branch so an offline run measures what the fallback
    actually ships, not a re-implementation of the harness baseline.
    """
    out: list[list[int]] = []
    for vec in queries:
        v = np.asarray(vec, dtype=np.float32)
        denom = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(v) or 1.0)
        denom[denom == 0] = 1.0
        cos = (matrix @ v) / denom
        order = np.argsort(-cos, kind="stable")
        out.append([int(i) for i in order[:k]])
    return out


def recall_at_k(candidates: list[list[int]], truth: list[list[int]]) -> float:
    """Mean fraction of the exact top-k each candidate list recovers (queries without truth are skipped)."""
    scores = [len(set(c) & set(t)) / len(t) for c, t in zip(candidates, truth) if t]
    return sum(scores) / len(scores) if scores else 0.0


def decision(recall: float, numpy_p95_ms: float | None, candidate_p95_ms: float | None, min_recall: float) -> dict:
    """Feature 19 adopt rule: adopt HNSW only if recall >= min_recall AND p95 improves."""
    adopt = (
        candidate_p95_ms is not None
        and numpy_p95_ms is not None
        and recall >= min_recall
        and candidate_p95_ms < numpy_p95_ms
    )
    return {
        "adopt_hnsw": adopt,
        "verdict": "ADOPT-HNSW" if adopt else "KEEP-NUMPY",
        "recall_vs_exact": recall,
        "min_recall": min_recall,
        "numpy_p95_ms": numpy_p95_ms,
        "candidate_p95_ms": candidate_p95_ms,
    }


def synthetic_corpus(n_chunks: int, dim: int, n_queries: int, seed: int = 2026) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    matrix = rng.normal(size=(n_chunks, dim)).astype(np.float32)
    queries = rng.normal(size=(n_queries, dim)).astype(np.float32)
    return matrix, queries


async def load_real_corpus() -> tuple[list[str], np.ndarray] | None:
    """The live chunk corpus (ids + embedding matrix); None when the DB is down or empty."""
    try:
        from core import db

        rows = await db.q("SELECT id, embedding FROM chunk;")
        rows = [r for r in rows if r.get("embedding")]
        if not rows:
            return None
        ids = [str(r["id"]) for r in rows]
        matrix = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
        return ids, matrix
    except Exception:  # noqa: BLE001
        return None


async def measure_hnsw(ids: list[str], matrix: np.ndarray, queries: np.ndarray, k: int, rounds: int = 3):
    """Real knn() per query, chunk ids mapped back to matrix row indices; returns (candidates, p95)."""
    from core.retrieve import knn

    lat: list[float] = []
    candidates: list[list[int]] = []
    for q in queries:
        t0 = time.perf_counter()
        rows = await knn(q.tolist(), k)
        lat.append((time.perf_counter() - t0) * 1000.0)
        candidates.append([ids.index(str(r["id"])) for r in rows[:k] if str(r["id"]) in ids])
    for _ in range(rounds - 1):  # p95 over warmed repeats
        for q in queries:
            t0 = time.perf_counter()
            await knn(q.tolist(), k)
            lat.append((time.perf_counter() - t0) * 1000.0)
    return candidates, pctl(lat, 0.95)


async def evaluate(n_chunks: int, dim: int, n_queries: int, k: int, min_recall: float, rounds: int) -> dict:
    real = await load_real_corpus()
    live = real is not None
    if live:
        ids, matrix = real
        rng = np.random.default_rng(2026)
        queries = rng.normal(size=(n_queries, matrix.shape[1])).astype(np.float32)
        corpus = {"source": "db-chunks", "chunks": len(ids), "dim": int(matrix.shape[1]), "queries": n_queries, "k": k}
    else:
        ids = [f"synthetic:{i}" for i in range(n_chunks)]
        matrix, queries = synthetic_corpus(n_chunks, dim, n_queries)
        corpus = {"source": "synthetic", "chunks": n_chunks, "dim": dim, "queries": n_queries, "k": k}

    base_lat: list[float] = []
    truth: list[list[int]] = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        truth = topk_exact(matrix, queries, k)
        base_lat.append((time.perf_counter() - t0) * 1000.0)
    numpy_p95 = pctl(base_lat, 0.95)

    report: dict = {"corpus": corpus, "numpy_baseline": {"p95_ms": numpy_p95, "rounds": rounds}}

    if live:
        candidates, cand_p95 = await measure_hnsw(ids, matrix, queries, k)
        recall = recall_at_k(candidates, truth)
        report["candidate_backend"] = "surrealdb-hnsw"
        report["decision"] = decision(recall, numpy_p95, cand_p95, min_recall)
        report["notes"] = "Live run over the real chunk corpus; verdict is measured."
    else:
        # Offline: verify the shipped fallback's ranking math against the exact baseline
        # (parity recall must be 1.0), then force KEEP-NUMPY — HNSW is unmeasured here.
        t0 = time.perf_counter()
        fb = fallback_scan(matrix, queries, k)
        fb_p95 = pctl([(time.perf_counter() - t0) * 1000.0 / max(len(queries), 1)], 0.95)
        parity = recall_at_k(fb, truth)
        report["candidate_backend"] = "numpy-fallback"
        report["fallback_parity_recall"] = parity
        report["fallback_p95_ms"] = fb_p95
        report["decision"] = decision(parity, numpy_p95, None, min_recall)
        report["notes"] = (
            "SurrealDB unreachable in this environment: HNSW is unmeasured and the shipped "
            "NumPy fallback is retained. Re-run with SurrealDB up for a live ADOPT/KEEP verdict. "
            f"Fallback parity vs exact baseline: recall {parity} (must be 1.0)."
        )
    return report


async def main() -> int:
    ap = argparse.ArgumentParser(description="HNSW vs NumPy retrieval evaluation (feature 19).")
    ap.add_argument("--chunks", type=int, default=2000, help="Synthetic corpus size (offline mode).")
    ap.add_argument("--dim", type=int, default=1024, help="Synthetic vector dim (offline mode).")
    ap.add_argument("--queries", type=int, default=50)
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--min-recall", type=float, default=1.0, help="Recall floor vs the exact NumPy baseline.")
    args = ap.parse_args()

    report = await evaluate(args.chunks, args.dim, args.queries, args.k, args.min_recall, args.rounds)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")
    d = report["decision"]
    print(f"corpus            : {report['corpus']['source']} ({report['corpus']['chunks']} chunks)")
    print(f"candidate backend : {report['candidate_backend']}")
    if "fallback_parity_recall" in report:
        print(f"fallback parity   : recall {report['fallback_parity_recall']} vs exact baseline")
    print(f"exact numpy p95   : {d['numpy_p95_ms']:.1f} ms" if d["numpy_p95_ms"] else "exact numpy p95   : n/a")
    print(f"candidate p95     : {d['candidate_p95_ms']:.1f} ms" if d["candidate_p95_ms"] else "candidate p95     : unmeasured")
    print(f"verdict           : {d['verdict']}")
    print(f"report            : {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
