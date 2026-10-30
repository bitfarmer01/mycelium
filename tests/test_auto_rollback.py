"""Feature 47: a promoted slot whose follow-up fitness drops below the documented floor reverts automatically.

The floor is the documented retrieval fitness floor (CONFIG["retrieval_fitness_floor"] = 0.5,
docs/required-features.md's config record), overridable via EXAM_ROLLBACK_FLOOR. Every revert
lands as one `revert` ledger event.
"""
from __future__ import annotations

from pathlib import Path

import pytest

BASELINE = (Path(__file__).resolve().parents[1] / "prompts" / "slot.md").read_text()

PACKS = [{"question_id": "q0", "text": "q0", "context": "c0", "n_docs": 3},
         {"question_id": "q1", "text": "q1", "context": "c1", "n_docs": 3}]


class HighRunner:
    """Scores fully: every answer's screen contains the scoring terms."""

    def __init__(self):
        self.runs = 0

    async def run(self, slot: str, packs: list[dict], label: str) -> dict:
        self.runs += 1
        return {"runner": "fake", "sandbox_id": "s", "answers": [
            {"question_id": p["question_id"], "ms": 0,
             "result": {"spoken": "x", "screen": "alpha beta", "claims": []}} for p in packs]}


class LowRunner:
    """Scores zero: answers carry no result."""

    def __init__(self):
        self.runs = 0

    async def run(self, slot: str, packs: list[dict], label: str) -> dict:
        self.runs += 1
        return {"runner": "fake", "sandbox_id": "s", "answers": [
            {"question_id": p["question_id"], "result": None, "ms": 0} for p in packs]}


class ExplodingRunner:
    async def run(self, slot: str, packs: list[dict], label: str) -> dict:
        raise AssertionError("runner must not be called")


@pytest.fixture
def slot_env(monkeypatch, tmp_path):
    """Slot and version files resolved from tmp (cwd-relative when present), repo slot as baseline."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "prompts" / "slot.md").write_text(BASELINE)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def exam_files(monkeypatch):
    """Growth's JSON inputs: one clean candidate, zero-baseline, two packs, known terms."""
    from core import growth

    files = {
        "candidates.json": {"suffixes": [{"name": "cand-0", "axis": "test", "text": "Apply rule zero."}]},
        "baseline_runs.json": {"runs": [[0.0, 0.0]], "runner": "fake"},
        "heldout_packs.json": PACKS,
        "terms.json": {"q0": ["alpha"], "q1": ["beta"]},
    }
    monkeypatch.setattr(growth, "_load_json", lambda name: files.get(name))


def snapshot_file(tmp_path) -> Path:
    return tmp_path / "slot_pre_burst.json"


async def test_promotion_below_floor_reverts_slot_and_records_ledger(slot_env, fake_db, exam_files):
    from core import exam, growth

    exam.snapshot_slot()
    candidate_slot = BASELINE.replace("## process\n", "## process\nAlways answer cheerfully.\n")
    assert candidate_slot != BASELINE
    growth.promote(candidate_slot, "1.6")

    out = await exam.check_rollback(runner=LowRunner(), candidate="cite-every-claim")

    assert out["reverted"] is True
    assert out["version"] == "1.0", "the report names the restored snapshot version"
    assert out["followup_mean"] == 0.0 and out["floor"] == 0.5
    assert growth.load_slot() == BASELINE, "the pre-burst slot text is restored"
    assert growth.slot_version() == "1.0", "the pre-burst version is restored"

    reverts = [v["row"] for sql, v in fake_db.calls if "CREATE ledger" in sql]
    assert len(reverts) == 1, "exactly one revert event records the rollback"
    row = reverts[0]
    assert row["type"] == "revert"
    assert row["target"] == "candidate:cite-every-claim"
    assert row["target_kind"] == "candidate"
    assert row["delta"] == pytest.approx(-0.5), "delta records the shortfall (mean - floor)"
    assert row["before"] == 0.0 and row["after"] == 0.5
    assert "auto-rollback" in row["cause"] and "0.500" in row["cause"]
    assert exam.read_snapshot(snapshot_file(slot_env)) is None, "the rollback window closes"


async def test_followup_at_or_above_floor_keeps_the_promotion(slot_env, fake_db, exam_files):
    from core import exam, growth

    exam.snapshot_slot()
    candidate_slot = BASELINE.replace("## process\n", "## process\nAlways answer cheerfully.\n")
    growth.promote(candidate_slot, "1.6")

    out = await exam.check_rollback(runner=HighRunner(), candidate="cand-0")

    assert out["reverted"] is False
    assert out["followup_mean"] == 1.0
    assert growth.load_slot() == candidate_slot, "a passing promotion stands"
    assert growth.slot_version() == "1.6"
    assert not [v for sql, v in fake_db.calls if "CREATE ledger" in sql], "no ledger writes on a pass"
    assert exam.read_snapshot(snapshot_file(slot_env)) is None, "rollback window closed"


async def test_check_without_snapshot_is_a_noop(slot_env, fake_db):
    from core import exam

    out = await exam.check_rollback(runner=ExplodingRunner())
    assert out["reverted"] is False and "no pre-burst snapshot" in out["reason"]
    assert fake_db.calls == []


async def test_check_at_snapshot_version_is_a_noop(slot_env, fake_db):
    from core import exam

    exam.snapshot_slot()
    version = exam.growth_version()
    out = await exam.check_rollback(runner=ExplodingRunner())
    assert out["reverted"] is False and "still at snapshot version" in out["reason"]
    assert exam.growth_version() == version
    assert fake_db.calls == []


async def test_check_without_heldout_packs_is_a_noop(slot_env, fake_db, monkeypatch):
    from core import exam, growth

    exam.snapshot_slot()
    growth.promote(BASELINE + "\n", "1.2")
    monkeypatch.setattr(growth, "_load_json", lambda name: {} if name == "terms.json" else None)

    out = await exam.check_rollback(runner=ExplodingRunner())
    assert out["reverted"] is False and "no held-out packs" in out["reason"]
    assert growth.slot_version() == "1.2", "unevaluated promotions are not reverted"
    assert exam.read_snapshot(snapshot_file(slot_env)) is not None, "rollback window stays open"
    assert fake_db.calls == []


async def test_burst_promotes_then_rolls_back_when_followup_fails(slot_env, fake_db, monkeypatch, exam_files):
    """End to end: improve_burst promotes, the follow-up scores below the floor, the slot reverts."""
    from core import growth
    from core.exam import improve_burst

    runners = iter([HighRunner(), LowRunner()])
    monkeypatch.setattr("core.runner.make_runner", lambda: next(runners))

    out = await improve_burst()

    assert out["slot_version"] == "1.0", "version is back at the pre-burst value"
    assert out["rollback"]["reverted"] is True and out["rollback"]["followup_mean"] == 0.0
    assert growth.load_slot() == BASELINE
    types = [v["row"]["type"] for sql, v in fake_db.calls if "CREATE ledger" in sql]
    assert "promote" in types and "revert" in types, "the burst promotes AND the ledger records the revert"


async def test_burst_promotion_passing_its_followup_stands(slot_env, fake_db, monkeypatch, exam_files):
    from core import growth
    from core.exam import improve_burst

    runners = iter([HighRunner(), HighRunner()])
    monkeypatch.setattr("core.runner.make_runner", lambda: next(runners))

    out = await improve_burst()

    assert out["slot_version"] == "1.1"
    assert out["rollback"]["reverted"] is False
    assert growth.slot_version() == "1.1"
    types = [v["row"]["type"] for sql, v in fake_db.calls if "CREATE ledger" in sql]
    assert "revert" not in types


def test_floor_is_env_configurable(monkeypatch):
    from core.exam import rollback_floor

    monkeypatch.delenv("EXAM_ROLLBACK_FLOOR", raising=False)
    assert rollback_floor() == 0.5, "default is the documented retrieval fitness floor"
    monkeypatch.setenv("EXAM_ROLLBACK_FLOOR", "0.7")
    assert rollback_floor() == 0.7
    monkeypatch.setenv("EXAM_ROLLBACK_FLOOR", "not-a-number")
    assert rollback_floor() == 0.5
