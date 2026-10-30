"""Baseline: k_runs exam runs of the current slot over the held-out packs; writes data/baseline_runs.json.

--retrieval-only (feature 14/16 guard): no LLM, no SurrealDB. Rebuilds the chunk corpus from
data/docs (embeddings via the persistent EmbeddingCache), ranks every held-out question two ways
— cosine-only rerank (the pre-hybrid baseline path) and hybrid cosine+BM25 — and reports hit@1/hit@3
against data/terms.json, plus a keyword-only probe per question (query = its expected terms).
Exits 1 if hybrid regresses hit@1 on question queries.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")  # values go to os.environ only; never printed

from core import db, runner  # noqa: E402
from core.contracts import CONFIG  # noqa: E402
from core.retrieve import hybrid_rerank, rerank

DATA = ROOT / "data"


async def main() -> int:
    if runner.PACKS_PATH.exists():
        packs = json.loads(runner.PACKS_PATH.read_text())
    else:
        missing = [p.name for p in (DATA / "heldout.json", DATA / "questions.json") if not p.exists()]
        if missing:
            print(f"run_baseline: missing {', '.join(missing)} (run Task 1 ingest/make_split first)", file=sys.stderr)
            return 2
        try:
            packs = await runner.build_packs()
        except Exception as e:  # noqa: BLE001
            print(f"run_baseline: build_packs failed ({type(e).__name__}: {e}); is SurrealDB up with chunks?",
                  file=sys.stderr)
            return 2
    if not packs:
        print("run_baseline: no held-out packs", file=sys.stderr)
        return 2

    from core import growth

    terms = json.loads((DATA / "terms.json").read_text()) if (DATA / "terms.json").exists() else {}
    n_docs = {p["question_id"]: p.get("n_docs", 0) for p in packs}
    slot = growth.load_slot()
    r = runner.make_runner()
    results = await asyncio.gather(*(r.run(slot, packs, f"baseline-{i}") for i in range(CONFIG["k_runs"])))

    runs = []
    for res in results:
        scores = []
        for a in res["answers"]:
            qid = a["question_id"]
            scores.append(float(growth.score_answer(a["result"], terms.get(qid, []), n_docs.get(qid, 0)))
                          if a["result"] else 0.0)
        runs.append(scores)
    means = [statistics.fmean(s) if s else 0.0 for s in runs]
    spread = max(means) - min(means)
    mean = statistics.fmean(means)
    kind = results[0]["runner"]
    out = {"runs": runs, "means": means, "spread": spread, "runner": kind,
           "sandbox_ids": [res["sandbox_id"] for res in results], "ts": db.now()}
    (DATA / "baseline_runs.json").write_text(json.dumps(out, indent=2))
    print(f"baseline mean={mean:.3f} spread={spread:.3f} runner={kind}")
    return 0


# ---------------------------------------------------------------------------------------------------- retrieval-only eval
def eval_rankings(rows: list[dict], query: str, floor: float, weight: float) -> tuple[list[dict], list[dict]]:
    """Rank the candidate rows two ways — cosine-only rerank vs hybrid cosine+BM25. Fresh copies per scorer.

    Rows must carry 'cosine' (and the alpha/beta fitness counts). Returns (baseline_ranked, hybrid_ranked).
    """
    baseline = rerank(copy.deepcopy(rows), floor)
    hybrid = hybrid_rerank(copy.deepcopy(rows), query, floor, weight)
    return baseline, hybrid


def is_relevant(row: dict, terms: list[str]) -> bool:
    """A chunk is 'right' for a question when its text names any expected term (case-insensitive)."""
    doc = (row.get("document") or "").lower()
    return any(t.lower() in doc for t in terms)


def hit_at_k(ranked: list[dict], terms: list[str], k: int) -> int:
    """1 if any of the top-k ranked rows is relevant, else 0."""
    return int(any(is_relevant(row, terms) for row in ranked[:k]))


def retrieval_eval() -> int:
    """Eval-split ranking comparison: cosine-only vs hybrid over the frozen held-out split. No LLM, no SurrealDB."""
    from core import retrieve

    sys.path.insert(0, str(ROOT / "scripts"))
    import ingest

    files = ingest.doc_files()
    if not files:
        print("retrieval-eval: no documents under data/docs", file=sys.stderr)
        return 2
    print(f"retrieval-eval: building corpus from {len(files)} files (embeddings via EmbeddingCache)…", flush=True)
    chunks, _sources = ingest.build_chunks(files, cache=ingest.EMBED_CACHE)
    if not chunks:
        print("retrieval-eval: corpus produced zero chunks", file=sys.stderr)
        return 2

    raw = json.loads((DATA / "questions.json").read_text())
    questions = raw["questions"] if isinstance(raw, dict) else raw
    heldout = json.loads((DATA / "heldout.json").read_text())
    terms = json.loads((DATA / "terms.json").read_text()) if (DATA / "terms.json").exists() else {}
    floor = CONFIG["retrieval_fitness_floor"]
    weight = CONFIG.get("bm25_weight", retrieve.BM25_WEIGHT)

    corpus_vecs = np.asarray([c["embedding"] for c in chunks], dtype=np.float32)
    results = []
    for qid in heldout:
        q = next((x for x in questions if str(x["id"]) == str(qid)), None)
        expected = terms.get(str(qid), [])
        if q is None or not expected:
            continue
        text = q["text"]
        qv = np.asarray(retrieve.embed_query(text), dtype=np.float32)
        sims = corpus_vecs @ qv
        rows = [
            {"id": c["id"], "document": c["document"], "title": c["title"], "source_link": c["source_link"],
             "alpha": c["alpha"], "beta": c["beta"], "cosine": float(s)}
            for c, s in zip(chunks, sims)
        ]
        baseline, hybrid = eval_rankings(rows, text, floor, weight)
        probe = " ".join(expected)  # keyword-only query: exact product terms, no phrasing
        baseline_p, hybrid_p = eval_rankings(rows, probe, floor, weight)
        results.append({
            "question_id": str(qid),
            "question": text,
            "expected_terms": expected,
            "baseline": {"hit@1": hit_at_k(baseline, expected, 1), "hit@3": hit_at_k(baseline, expected, 3)},
            "hybrid": {"hit@1": hit_at_k(hybrid, expected, 1), "hit@3": hit_at_k(hybrid, expected, 3)},
            "keyword_probe": {
                "query": probe,
                "baseline_hit@1": hit_at_k(baseline_p, expected, 1),
                "hybrid_hit@1": hit_at_k(hybrid_p, expected, 1),
            },
        })

    if not results:
        print("retrieval-eval: no held-out questions with expected terms", file=sys.stderr)
        return 2
    n = len(results)
    summary = {
        "n_questions": n,
        "n_chunks": len(chunks),
        "bm25_weight": weight,
        "baseline_hit@1": statistics.fmean(x["baseline"]["hit@1"] for x in results),
        "hybrid_hit@1": statistics.fmean(x["hybrid"]["hit@1"] for x in results),
        "baseline_hit@3": statistics.fmean(x["baseline"]["hit@3"] for x in results),
        "hybrid_hit@3": statistics.fmean(x["hybrid"]["hit@3"] for x in results),
        "keyword_probe_baseline_hit@1": statistics.fmean(x["keyword_probe"]["baseline_hit@1"] for x in results),
        "keyword_probe_hybrid_hit@1": statistics.fmean(x["keyword_probe"]["hybrid_hit@1"] for x in results),
    }
    (DATA / "retrieval_eval.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(f"retrieval-eval: n={n} chunks={len(chunks)} weight={weight}")
    print(f"  question queries  hit@1 baseline={summary['baseline_hit@1']:.3f} hybrid={summary['hybrid_hit@1']:.3f}")
    print(f"  question queries  hit@3 baseline={summary['baseline_hit@3']:.3f} hybrid={summary['hybrid_hit@3']:.3f}")
    print(f"  keyword-only probe hit@1 baseline={summary['keyword_probe_baseline_hit@1']:.3f} "
          f"hybrid={summary['keyword_probe_hybrid_hit@1']:.3f}")
    if summary["hybrid_hit@1"] < summary["baseline_hit@1"]:
        print("retrieval-eval: REGRESSION — hybrid hit@1 below baseline on question queries", file=sys.stderr)
        return 1
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--retrieval-only", action="store_true", help="ranking eval over the eval split; no LLM, no DB")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(retrieval_eval() if args.retrieval_only else asyncio.run(main()))
