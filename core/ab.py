"""A/B harness: score two slot variants over the same held-out packs and emit a winner report.

Report-only by construction — nothing here writes the production slot (prompts/slot.md). Putting a
winner into production is an explicit growth.promote() call (the /improve gate or an operator), so a
harness run can never change behavior on its own.

Two comparison shapes share one report:
- slot vs slot: both variants are scored fresh through the runner (default).
- fresh vs stored baseline: pass `baseline_runs` (e.g. data/baseline_runs.json — the all-Opus
  reference) and only variant B is scored; the routing tolerance check (feature 27) is this mode
  with MYCELIUM_CHEAP_MODEL set.
"""
from __future__ import annotations

import time

from core import growth
from core.runner import Runner


def score_variant(answers: list[dict], packs: list[dict], terms: dict) -> list[float]:
    """Per-question growth.score_answer for one variant run; failed/missing answers score 0.0."""
    n_docs = {p["question_id"]: p.get("n_docs", 0) for p in packs}
    by_q = {a.get("question_id"): a.get("result") for a in answers}
    return [float(growth.score_answer(by_q.get(p["question_id"]), terms.get(p["question_id"], []),
                                      n_docs.get(p["question_id"], 0)))
            for p in packs]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def winner_report(runs_a: list[list[float]], runs_b: list[list[float]], name_a: str, name_b: str,
                  tol: float = 0.0) -> dict:
    """Pure comparison of two variants' score runs: means, delta, winner, tolerance verdict.

    tol: how far below A a B mean may land and still count as within tolerance of the baseline; a
    routing eval passes --tol at or above the all-Opus baseline's own run spread. 0.0 is the strict
    default: B must not regress at all. Winner is the higher mean of means.
    """
    mean_a = _mean([_mean(r) for r in runs_a])
    mean_b = _mean([_mean(r) for r in runs_b])
    delta = mean_b - mean_a
    return {
        "ts": time.time(),
        "a": {"name": name_a, "runs": runs_a, "mean": mean_a},
        "b": {"name": name_b, "runs": runs_b, "mean": mean_b},
        "delta": delta,
        "tolerance": tol,
        "within_tolerance": delta >= -tol,
        "winner": name_b if delta > 0 else (name_a if delta < 0 else "tie"),
        "promoted": False,
        "note": "report only; production slot changes require an explicit growth.promote()",
    }


async def _score_run(slot: str, packs: list[dict], terms: dict, runner: Runner, label: str) -> list[float]:
    res = await runner.run(slot, packs, label)
    return score_variant(res["answers"], packs, terms)


async def run_ab(slot_a: str, slot_b: str, packs: list[dict], terms: dict, runner: Runner, *,
                 runs: int = 1, name_a: str = "A", name_b: str = "B", tol: float = 0.0,
                 baseline_runs: list[list[float]] | None = None) -> dict:
    """Score both variants on the eval split with the given runner and return the winner report.

    baseline_runs: stored reference scores for A (no A run is executed when given).
    """
    if baseline_runs is not None:
        runs_a = baseline_runs
    else:
        runs_a = [await _score_run(slot_a, packs, terms, runner, f"{name_a}-{i}") for i in range(runs)]
    runs_b = [await _score_run(slot_b, packs, terms, runner, f"{name_b}-{i}") for i in range(runs)]
    return winner_report(runs_a, runs_b, name_a, name_b, tol=tol)
