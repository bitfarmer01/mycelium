"""Feature 33 — shape-prior decay toward uniform: configurable, OFF by default until tuned."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from core import learn
from core.contracts import CONFIG


def test_decay_off_by_default_is_identity():
    assert CONFIG["prior_decay_per_day"] == 0.0
    prior = [0.6, 0.25, 0.15]
    assert learn.prior_decay(prior, 28) == prior  # off = no-op


def test_decay_priors_off_issues_no_queries(fake_db):
    async def run():
        return await learn.decay_priors()

    assert asyncio.run(run()) == 0
    assert fake_db.sql() == []  # a disabled decay never touches the DB


def test_four_week_prior_decays_by_documented_factor():
    """28 days at the spec's 1%/day retains (0.99)^28 of the deviation from uniform."""
    uniform = 1 / 3
    prior = [0.6, 0.25, 0.15]
    after = learn.prior_decay(prior, 28, rate=0.01)
    factor = 0.99 ** 28
    for x0, x1 in zip(prior, after):
        assert (x1 - uniform) == pytest.approx((x0 - uniform) * factor, abs=1e-12)
    assert sum(after) == pytest.approx(1.0)
    assert after[0] < prior[0] and after[1] > prior[1] and after[2] > prior[2]


def test_decay_rate_is_configurable():
    prior = [0.6, 0.25, 0.15]
    slow = learn.prior_decay(prior, 28, rate=0.01)
    fast = learn.prior_decay(prior, 28, rate=0.05)
    assert fast[0] < slow[0] < prior[0]  # a higher rate pulls harder toward uniform
    assert learn.prior_decay(prior, 0, rate=0.01) == prior
    assert learn.prior_decay(prior, -5, rate=0.01) == prior  # negative age never moves


def test_decay_priors_moves_aged_rows_and_ledgers(fake_db, monkeypatch):
    monkeypatch.setitem(CONFIG, "prior_decay_per_day", 0.01)
    old = (datetime.now(UTC) - timedelta(days=28)).isoformat()
    fake_db.rows["FROM question_shape"] = [
        {"id": "question_shape:who-owns", "shape": "who-owns",
         "prior": [0.6, 0.25, 0.15], "prior_ts": old},
        {"id": "question_shape:factual", "shape": "factual",
         "prior": [1 / 3] * 3, "prior_ts": old},  # already uniform: no row, no ledger
    ]
    fake_db.rows["CREATE ledger"] = [{"id": "ledger:1"}]

    n = asyncio.run(learn.decay_priors())

    assert n == 1
    ups = [(s, v) for s, v in fake_db.calls if s.startswith("UPSERT question_shape:")]
    assert ups and ups[0][1]["prior"][0] == pytest.approx(1 / 3 + (0.6 - 1 / 3) * 0.99 ** 28)
    lids = [(s, v) for s, v in fake_db.calls if s.startswith("CREATE ledger")]
    assert len(lids) == 1
    row = lids[0][1]["row"]
    assert row["type"] == "decay" and row["target_kind"] == "shape" and row["target"] == "who-owns"
    assert "prior decay" in row["cause"]
    assert fake_db.rows["FROM question_shape"]  # untouched fixture guard


def test_decay_priors_skips_rows_without_ts(fake_db, monkeypatch):
    monkeypatch.setitem(CONFIG, "prior_decay_per_day", 0.01)
    fake_db.rows["FROM question_shape"] = [
        {"id": "question_shape:who-owns", "shape": "who-owns", "prior": [0.6, 0.25, 0.15]},
    ]
    assert asyncio.run(learn.decay_priors()) == 0  # unknown age → never decays
    assert not any(s.startswith("UPSERT") for s in fake_db.sql())


def test_decay_tick_includes_prior_decay_when_enabled(fake_db, monkeypatch):
    monkeypatch.setitem(CONFIG, "prior_decay_per_day", 0.01)
    fake_db.rows["FROM config:main"] = [{"last_tick": None}]
    fake_db.rows["FROM chunk WHERE"] = []
    old = (datetime.now(UTC) - timedelta(days=28)).isoformat()
    fake_db.rows["FROM question_shape"] = [
        {"id": "question_shape:who-owns", "shape": "who-owns",
         "prior": [0.6, 0.25, 0.15], "prior_ts": old},
    ]
    fake_db.rows["CREATE ledger"] = [{"id": "ledger:1"}]
    n = asyncio.run(learn.decay_tick())
    assert n == 1  # the one prior decay, from the one aged row


def test_age_days_parses_and_never_negative():
    now = "2026-10-30T00:00:00+00:00"
    assert learn._age_days("2026-10-28T00:00:00+00:00", now) == pytest.approx(2.0)
    assert learn._age_days("2026-11-30T00:00:00+00:00", now) == 0.0  # future ts
    assert learn._age_days(None, now) == 0.0
    assert learn._age_days("not-a-date", now) == 0.0
