"""Tests for core.learn: Beta fitness, shape prior EMA, ledger writes, correction safety, decay."""
from __future__ import annotations

import asyncio
import random
import re

import pytest

from core import learn
from core.contracts import CONFIG, ROLES


def test_beta_confirm_moves_half_to_two_thirds():
    assert learn.fitness(1, 1) == pytest.approx(0.5)
    assert learn.uncertainty(1, 1) == pytest.approx(0.333, abs=0.01)
    a, b = learn.beta_update(1, 1, 1.0)
    assert learn.fitness(a, b) == pytest.approx(0.667, abs=0.001)
    assert learn.uncertainty(a, b) == pytest.approx(0.222, abs=0.01)


def test_beta_correct_moves_to_third():
    a, b = learn.beta_update(1, 1, 0.0)
    assert (a, b) == (1, 2)
    assert learn.fitness(a, b) == pytest.approx(1 / 3, abs=0.001)


def test_fitness_never_leaves_unit_interval():
    rng = random.Random(0)
    a, b = 1.0, 1.0
    for _ in range(1000):
        a, b = learn.beta_update(a, b, rng.random())
        f = learn.fitness(a, b)
        assert 0 < f < 1
        assert a >= 1 and b >= 1
        assert 0 <= learn.uncertainty(a, b) <= 1.0001


def test_prior_one_correction_033_to_053():
    p = learn.prior_update([1 / 3] * 3, 1, CONFIG["prior_rate_correct"])
    assert p[1] == pytest.approx(0.533, abs=0.001)
    assert sum(p) == pytest.approx(1.0)


def test_prior_floor_and_sum_one():
    p = [1 / 3] * 3
    for _ in range(100):
        p = learn.prior_update(p, 0, 0.3)
    assert sum(p) == pytest.approx(1.0)
    assert min(p) >= CONFIG["prior_floor"] - 1e-9
    assert p[0] == max(p)


def _router(state):
    """Fake SurrealDB: question with two cited chunks from one source."""

    def route(sql, vars):
        if "->cites->chunk" in sql:
            return [{"c": ["chunk:a", "chunk:b"]}]
        if sql.startswith("SELECT * FROM question:"):
            return [{"id": "question:q1", "shape": "who-owns", "taken": "literal"}]
        if sql.startswith("SELECT prior FROM question_shape"):
            return [{"prior": [1 / 3] * 3}]
        m = re.match(r"SELECT \* FROM (chunk:\w+)", sql)
        if m:
            cid = m.group(1)
            return [{"id": cid, **state[cid]}]
        if sql.startswith("SELECT alpha, beta FROM chunk WHERE source"):
            return [{"alpha": s["alpha"], "beta": s["beta"]} for s in state.values()]
        m = re.match(r"UPDATE (chunk:\w+) SET", sql)
        if m:
            state[m.group(1)].update(alpha=vars["alpha"], beta=vars["beta"])
            return [{"id": m.group(1)}]
        if sql.startswith("CREATE ledger"):
            return [{"id": f"ledger:{len(state['_ledger']) if '_ledger' in state else 0}"}]
        if sql.startswith("CREATE"):
            return [{"id": sql.split()[1] + ":x"}]
        return []

    return route


def test_correction_never_raises_fitness(fake_db):
    state = {
        "chunk:a": {"alpha": 5.0, "beta": 1.0, "source": "source:s1"},
        "chunk:b": {"alpha": 1.0, "beta": 1.0, "source": "source:s1"},
    }
    before = {k: learn.fitness(v["alpha"], v["beta"]) for k, v in state.items()}
    fake_db.rows[""] = _router(state)
    out = asyncio.run(learn.correct("question:q1", "intent"))

    for k, prior in before.items():
        assert learn.fitness(state[k]["alpha"], state[k]["beta"]) <= prior
    ledgers = [v["row"] for s, v in fake_db.calls if s.startswith("CREATE ledger")]
    correct_rows = [r for r in ledgers if r["type"] == "correct"]
    assert {r["target"] for r in correct_rows if r["target_kind"] == "chunk"} == {"chunk:a", "chunk:b"}
    assert any(r["target_kind"] == "source" for r in correct_rows)
    for r in correct_rows:
        assert r["after"] <= r["before"]
        assert r["delta"] <= 0
        assert r["ts"]
    prior_rows = [r for r in ledgers if r["type"] == "prior"]
    assert prior_rows and prior_rows[0]["target"] == "who-owns" and prior_rows[0]["target_kind"] == "shape"
    assert prior_rows[0]["cause"] == "correction literal→intent"
    assert out["shape"] == "who-owns" and out["from_role"] == "literal" and out["to_role"] == "intent"
    assert len(out["prior_before"]) == len(ROLES) == len(out["prior_after"])
    assert out["prior_after"][1] == pytest.approx(0.533, abs=0.001)
    assert out["lesson_id"]
    assert len(out["ledger_ids"]) == len(ledgers)


def test_decay_moves_toward_half(fake_db):
    a, b = learn.decay_ab(5.0, 1.0)
    assert 0.5 < learn.fitness(a, b) < learn.fitness(5.0, 1.0)
    a, b = learn.decay_ab(1.0, 5.0)
    assert learn.fitness(1.0, 5.0) < learn.fitness(a, b) < 0.5

    rows = [
        {"id": "chunk:a", "alpha": 5.0, "beta": 1.0, "last_event": "2026-01-01T00:00:00+00:00"},
        {"id": "chunk:b", "alpha": 1.0, "beta": 3.0, "last_event": "2026-12-01T00:00:00+00:00"},
    ]
    fake_db.rows["FROM config:main"] = [{"last_tick": "2026-06-01T00:00:00+00:00"}]
    fake_db.rows["FROM chunk WHERE"] = rows
    fake_db.rows["CREATE ledger"] = [{"id": "ledger:1"}]
    n = asyncio.run(learn.decay_tick())
    assert n == 1  # chunk:b had an event after the last tick
    ups = [(s, v) for s, v in fake_db.calls if s.startswith("UPDATE chunk:a")]
    assert ups and ups[0][1]["alpha"] == pytest.approx(1 + 0.95 * 4)
    assert not any(s.startswith("UPDATE chunk:b") for s in fake_db.sql())
    assert any("last_tick" in s for s in fake_db.sql())



# ---------- golden values (feature 40) ----------
# These pin the exact EMA / Beta outputs so the documented formula can never
# drift silently. Golden values were computed from core/learn.py as merged on
# main; if one of these fails, the learning math changed — intentionally or not.

_G = pytest.approx  # readability


def test_golden_beta_update_and_uncertainty():
    # evidence 0.8 on (1,1): alpha gains 2*(0.8-0.5)=0.6
    assert learn.beta_update(1.0, 1.0, 0.8) == _G((1.6, 1.0), rel=1e-12)
    # evidence 0.0 on (2.5, 1.7): beta gains 2*0.5=1.0
    assert learn.beta_update(2.5, 1.7, 0.0) == _G((2.5, 2.7), rel=1e-12)
    # evidence clamps to [0,1]
    assert learn.beta_update(1.0, 1.0, 5.0) == _G((2.0, 1.0), rel=1e-12)
    assert learn.beta_update(1.0, 1.0, -3.0) == _G((1.0, 2.0), rel=1e-12)
    # closed-form variance-derived uncertainty
    assert learn.uncertainty(1.0, 1.0) == _G(1 / 3, rel=1e-12)
    assert learn.uncertainty(1.0, 2.0) == _G(2 / 9, rel=1e-12)


def test_golden_decay():
    assert learn.decay_ab(1.6, 1.2) == _G((1.57, 1.19), rel=1e-12)  # CONFIG decay 0.95
    assert learn.decay_ab(3.0, 1.0, 0.5) == _G((2.0, 1.0), rel=1e-12)  # explicit rate
    assert learn.decay_ab(1.0, 1.0) == _G((1.0, 1.0), rel=1e-12)  # uniform is the fixed point


def test_golden_prior_sequence_three_corrections():
    # Fixed sequence: three corrections toward "intent" (idx 1) from a uniform prior,
    # rate 0.3. Closed form for one move: p_r += r*(1-p_r), then normalized
    # (floor never binds here until rate 0.3 saturates; check exact values).
    p = [1 / 3] * 3
    for _ in range(3):
        p = learn.prior_update(p, 1, CONFIG["prior_rate_correct"])
    assert p == _G([0.11433333333333334, 0.7713333333333333, 0.11433333333333334], rel=1e-12)


def test_golden_prior_sequence_mixed_roles():
    # Fixed mixed sequence: intent, intent, intent, literal, scope, intent.
    # Bit-exact golden output pinned against the merged implementation.
    seq = [1, 1, 1, 0, 2, 1]
    p = [1 / 3] * 3
    for idx in seq:
        p = learn.prior_update(p, idx, CONFIG["prior_rate_correct"])
    assert p[0].hex() == "0x1.7d5efd2d2e7e0p-3"
    assert p[1].hex() == "0x1.210ef8321cfa5p-1"
    assert p[2].hex() == "0x1.fe65220a5d98ap-3"


def test_golden_prior_floor_engagement():
    # 100 corrections toward role 0 from uniform: everything except role 0 hits
    # the 0.05 floor; role 0 absorbs the remaining mass.
    p = [1 / 3] * 3
    for _ in range(100):
        p = learn.prior_update(p, 0, 0.3)
    assert p == _G([0.9, 0.05, 0.05], abs=1e-9)
