"""Feature 32 — a high-confidence correction moves the EMA prior further than a low-confidence one.

The pinned EMA math (prior_update, golden values in test_learn.py) is untouched:
confidence maps onto the rate at the flow level, so the golden values still hold.
"""
from __future__ import annotations

import asyncio

import pytest

from core import learn
from core.contracts import CONFIG, ROLES


def _correct_router(state):
    """Fake SurrealDB for learn.correct: one question, no cited chunks, stateful shape prior."""

    def route(sql, vars):
        if "->cites->chunk" in sql:
            return [{"c": []}]
        if sql.startswith("SELECT * FROM question:"):
            return [{"id": "question:q1", "shape": "who-owns", "taken": "literal"}]
        if sql.startswith("SELECT prior FROM question_shape"):
            return [{"prior": list(state["prior"])}]
        if "UPSERT question_shape" in sql:
            state["prior"] = list(vars["prior"])
            return [{"id": "question_shape:who-owns"}]
        if sql.startswith("CREATE ledger"):
            state["ledger"].append(dict(vars["row"]))
            return [{"id": f"ledger:{len(state['ledger'])}"}]
        if sql.startswith("CREATE"):
            return [{"id": "observation:x"}]
        return []

    return route


def _uniform() -> list[float]:
    return [1 / 3] * 3


def _delta(prior_before: list[float], prior_after: list[float], role: str) -> float:
    idx = ROLES.index(role)
    return prior_after[idx] - prior_before[idx]


def test_high_confidence_moves_prior_further_than_low(fake_db):
    high_state = {"prior": _uniform(), "ledger": []}
    low_state = {"prior": _uniform(), "ledger": []}
    fake_db.rows[""] = _correct_router(high_state)
    high = asyncio.run(learn.correct("question:q1", "intent", confidence=1.0))
    fake_db.rows[""] = _correct_router(low_state)
    low = asyncio.run(learn.correct("question:q1", "intent", confidence=0.4))

    d_high = _delta(high["prior_before"], high["prior_after"], "intent")
    d_low = _delta(low["prior_before"], low["prior_after"], "intent")
    assert d_high > d_low > 0


def test_exact_delta_is_the_pinned_rate_scaled_by_confidence(fake_db):
    state = {"prior": _uniform(), "ledger": []}
    fake_db.rows[""] = _correct_router(state)
    out = asyncio.run(learn.correct("question:q1", "intent", confidence=0.4))

    rate = learn.correction_rate(0.4)
    assert rate == pytest.approx(CONFIG["prior_rate_correct"] * 0.4, rel=1e-12)
    expected = learn.prior_update(_uniform(), ROLES.index("intent"), rate)[ROLES.index("intent")]
    d = _delta(out["prior_before"], out["prior_after"], "intent")
    assert d == pytest.approx(expected - 1 / 3, rel=1e-12)
    # the ledger row carries the same exact delta
    prior_rows = [r for r in state["ledger"] if r["type"] == "prior"]
    assert prior_rows[0]["delta"] == pytest.approx(d, rel=1e-12)


def test_default_confidence_preserves_pre_weighting_behavior(fake_db):
    state = {"prior": _uniform(), "ledger": []}
    fake_db.rows[""] = _correct_router(state)
    default = asyncio.run(learn.correct("question:q1", "intent"))

    assert learn.correction_rate(1.0) == pytest.approx(CONFIG["prior_rate_correct"], rel=1e-12)
    assert _delta(default["prior_before"], default["prior_after"], "intent") == pytest.approx(
        learn.prior_update(_uniform(), 1, CONFIG["prior_rate_correct"])[1] - 1 / 3, rel=1e-12)


def test_zero_confidence_moves_nothing(fake_db):
    state = {"prior": _uniform(), "ledger": []}
    fake_db.rows[""] = _correct_router(state)
    out = asyncio.run(learn.correct("question:q1", "intent", confidence=0.0))

    assert out["prior_after"] == pytest.approx(_uniform())
    assert _delta(out["prior_before"], out["prior_after"], "intent") == 0.0


def test_correction_rate_clamps_out_of_range_confidence():
    assert learn.correction_rate(-1.0) == 0.0
    assert learn.correction_rate(0.0) == 0.0
    assert learn.correction_rate(2.0) == pytest.approx(CONFIG["prior_rate_correct"], rel=1e-12)
    assert learn.correction_rate(0.5) == pytest.approx(0.15, rel=1e-12)


def test_confidence_is_recorded_on_the_observation(fake_db):
    state = {"prior": _uniform(), "ledger": []}
    fake_db.rows[""] = _correct_router(state)
    asyncio.run(learn.correct("question:q1", "intent", confidence=0.4))

    obs = [(s, v) for s, v in fake_db.calls if s.startswith("CREATE observation")]
    assert obs and obs[0][1]["row"]["confidence"] == pytest.approx(0.4)


def test_chained_corrections_stay_normalized_and_floored(fake_db):
    state = {"prior": _uniform(), "ledger": []}
    fake_db.rows[""] = _correct_router(state)
    for conf in (1.0, 0.2, 0.7):
        out = asyncio.run(learn.correct("question:q1", "intent", confidence=conf))
        state["prior"] = list(out["prior_after"])

    assert sum(state["prior"]) == pytest.approx(1.0)
    assert min(state["prior"]) >= CONFIG["prior_floor"] - 1e-9
