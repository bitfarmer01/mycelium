"""Feature 28: A/B harness — scores two slot variants on the eval split, emits a winner report,
and never changes the production slot without an explicit promote."""
from __future__ import annotations

import json

import pytest

from core import ab, growth

PACKS = [{"question_id": "q1", "n_docs": 2}, {"question_id": "q2", "n_docs": 2}]
TERMS = {"q1": ["alpha"], "q2": ["beta", "gamma"]}


def _ans(qid: str, screen: str) -> dict:
    return {"question_id": qid, "result": {"spoken": "s", "screen": screen, "claims": []}}


class FakeRunner:
    """Serves canned answers keyed by the slot text, in pack order."""

    def __init__(self, by_slot: dict[str, list[dict]]):
        self.by_slot = by_slot
        self.calls: list[tuple[str, str]] = []

    async def run(self, slot: str, packs: list[dict], label: str) -> dict:
        self.calls.append((slot, label))
        return {"runner": "fake", "sandbox_id": "fake", "answers": self.by_slot[slot], "ms": 0}


def test_score_variant_scores_term_matches():
    answers = [_ans("q1", "alpha mentioned here"), _ans("q2", "beta and gamma both here")]
    assert ab.score_variant(answers, PACKS, TERMS) == [1.0, 1.0]
    partial = [_ans("q1", "no term"), _ans("q2", "beta only")]
    assert ab.score_variant(partial, PACKS, TERMS) == [0.0, 0.5]
    failed = [{"question_id": "q1", "result": None}, {"question_id": "q2", "result": None}]
    assert ab.score_variant(failed, PACKS, TERMS) == [0.0, 0.0]


async def test_run_ab_names_winner_and_delta():
    slot_a, slot_b = "SLOT A {context} {question}", "SLOT B {context} {question}"
    runner = FakeRunner({
        slot_a: [_ans("q1", "nothing"), _ans("q2", "beta")],
        slot_b: [_ans("q1", "alpha"), _ans("q2", "beta gamma")],
    })
    report = await ab.run_ab(slot_a, slot_b, PACKS, TERMS, runner, name_a="base", name_b="cand")
    assert report["winner"] == "cand"
    assert report["a"]["mean"] == pytest.approx(0.25)
    assert report["delta"] == pytest.approx(0.75)
    assert report["promoted"] is False


async def test_tie_and_tolerance_fields():
    slot = "S {context} {question}"
    runner = FakeRunner({slot: [_ans("q1", "alpha"), _ans("q2", "beta gamma")]})
    report = await ab.run_ab(slot, slot, PACKS, TERMS, runner, name_a="x", name_b="y")
    assert report["winner"] == "tie"
    assert report["within_tolerance"] is True  # delta 0 is inside every tolerance

    worse = ab.winner_report([[0.8]], [[0.78]], "opus", "routed", tol=0.033)
    assert worse["winner"] == "opus"
    assert worse["within_tolerance"] is True  # a 0.02 dip inside the baseline spread
    strict = ab.winner_report([[0.8]], [[0.78]], "opus", "routed")
    assert strict["within_tolerance"] is False  # default tol=0: any regression fails


async def test_baseline_mode_skips_the_a_run():
    slot_a, slot_b = "A {context} {question}", "B {context} {question}"
    runner = FakeRunner({slot_b: [_ans("q1", "alpha"), _ans("q2", "beta gamma")]})
    report = await ab.run_ab(slot_a, slot_b, PACKS, TERMS, runner, name_a="opus", tol=0.1,
                             baseline_runs=[[0.8, 0.9], [0.7, 0.9]])
    assert len(runner.calls) == 1  # only B ran
    assert report["a"]["mean"] == pytest.approx(0.825)
    assert report["within_tolerance"] is True


async def test_harness_never_promotes(monkeypatch):
    def no_promote(*a, **k):
        raise AssertionError("the A/B harness must never change the production slot")

    monkeypatch.setattr(growth, "promote", no_promote)
    slot_a, slot_b = "A {context} {question}", "B {context} {question}"
    runner = FakeRunner({slot_a: [_ans("q1", "alpha"), _ans("q2", "")],
                         slot_b: [_ans("q1", "alpha"), _ans("q2", "beta gamma")]})
    report = await ab.run_ab(slot_a, slot_b, PACKS, TERMS, runner)
    assert report["promoted"] is False and report["winner"] == "B"


def test_report_serializes_to_json():
    r = ab.winner_report([[0.5]], [[0.7]], "a", "b")
    assert json.loads(json.dumps(r))["winner"] == "b"
