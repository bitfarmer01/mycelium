"""Task 6: slot, candidate pool, lint veto, E3 scorer, spread gate. No CLI, DB or network."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from core import growth

ROOT = Path(__file__).resolve().parents[1]
BASELINE = (ROOT / "prompts" / "slot.md").read_text()


def _pool_from_file() -> dict:
    p = ROOT / "data" / "candidates.json"
    return json.loads(p.read_text()) if p.exists() else growth.DEFAULT_POOL


def _with_process_line(text: str) -> str:
    return growth._insert_process(BASELINE, text)


def test_lint_vetoes_poison_names_pattern():
    poisons = list(growth.DEFAULT_POOL["poisons"]) + list(_pool_from_file().get("poisons", []))
    assert len(poisons) >= 3
    for p in poisons:
        ok, reason = growth.lint(_with_process_line(p["text"]), BASELINE)
        assert ok is False, p
        assert "forbidden pattern" in reason, (p, reason)
        assert "'" in reason  # pattern is named


def test_lint_passes_clean_suffixes():
    # Default pool only: the suffixes in data/candidates.json (DeepSeek 2) are 1.34-1.44x the short
    # baseline and fail CONFIG["slot_token_ratio"]; that is reported to the lead, not hidden here.
    for pool in (growth.DEFAULT_POOL,):
        for c in growth.candidate_pool(BASELINE, pool):
            if c["poison"] or c["axis"] == "composed":
                continue
            ok, reason = growth.lint(c["slot"], BASELINE)
            assert ok, (c["name"], reason)


def test_lint_composed_pair_exceeds_ratio():
    # Known and reported: the default composed pair is ~1.37x the short baseline (words and chars),
    # over CONFIG["slot_token_ratio"] 1.25, so it is vetoed by length, not by a forbidden pattern.
    composed = [c for c in growth.candidate_pool(BASELINE, growth.DEFAULT_POOL) if c["axis"] == "composed"]
    assert composed
    for c in composed:
        ok, reason = growth.lint(c["slot"], BASELINE)
        assert (ok, reason) == (False, "slot too long (ratio)")


def test_lint_frozen_section_change_vetoed():
    changed = BASELINE.replace("Answer the question from the numbered wiki context only.", "Answer freely.")
    ok, reason = growth.lint(changed, BASELINE)
    assert not ok and reason == "frozen section changed"
    ok, reason = growth.lint(BASELINE.replace("{context}", "the context"), BASELINE)
    assert not ok and reason == "missing '{context}'"


def test_score_answer_dangling_citation_zero():
    res = {"spoken": "short", "screen": "alpha beta", "claims": [{"text": "x", "chunk_ids": [4]}]}
    assert growth.score_answer(res, ["alpha"], 3) == 0.0
    assert growth.score_answer(None, ["alpha"], 3) == 0.0
    long = {"spoken": "w " * 121, "screen": "alpha", "claims": []}
    assert growth.score_answer(long, ["alpha"], 3) == 0.0


def test_score_answer_term_fraction():
    res = {"spoken": "ok", "screen": "Alpha and BETA here [1]", "claims": [{"text": "x", "chunk_ids": [1, 3]}]}
    assert growth.score_answer(res, ["alpha", "beta", "gamma"], 3) == pytest.approx(0.667, abs=1e-3)


def test_gate_pass_when_delta_above_spread():
    g = growth.gate([[0.7, 0.7], [0.72, 0.72], [0.68, 0.68]], [0.8, 0.8])
    assert g["verdict"] == "PASS"
    assert g["delta"] == pytest.approx(0.1)
    for k in ("baseline_mean", "spread", "ratio", "mean", "means", "bootstrap_half_width"):
        assert k in g


def test_gate_veto_within_spread():
    g = growth.gate([[0.7, 0.7], [0.72, 0.72], [0.68, 0.68]], [0.71, 0.71])
    assert g["verdict"] == "VETO"


def test_gate_zero_spread_uses_bootstrap():
    runs = [[0.5, 0.9, 0.7]] * 3
    g = growth.gate(runs, [0.501, 0.901, 0.701])
    assert g["bootstrap_half_width"] > 0
    assert g["spread"] > 0
    assert g["verdict"] == "VETO"
    # fully degenerate: nothing to beat, still never PASS on 0 > 0
    g0 = growth.gate([[0.5, 0.5]] * 3, [0.5, 0.5])
    assert g0["verdict"] == "VETO"


def test_promote_writes_only_slot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "data").mkdir()
    slot = tmp_path / "prompts" / "slot.md"
    slot.write_text(BASELINE)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    v = growth.promote(BASELINE + "\nextra", "1.1", path=str(slot))
    assert v == "1.1"
    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert after - before == {tmp_path / "data" / "slot_version.txt"}
    assert slot.read_text().endswith("extra")
    assert growth.slot_version() == "1.1"


def test_render_prompt_appends_contract_once():
    out = growth.render_prompt(BASELINE, "CTX", "Q?")
    assert out.endswith(growth.CONTRACT)
    assert out.count(growth.CONTRACT) == 1
    assert "CTX" in out and "Q?" in out and "{context}" not in out


async def test_improve_vetoes_poisons_without_runner(fake_db, monkeypatch, tmp_path):
    ledger, lessons, made = [], [], []

    async def write_ledger(row):
        ledger.append(row)
        return "ledger:x"

    async def write_lesson(*a, **kw):
        lessons.append((a, kw))
        return "lesson:x"

    fake_learn = types.ModuleType("core.learn")
    fake_learn.write_ledger = write_ledger
    fake_learn.write_lesson = write_lesson
    fake_runner = types.ModuleType("core.runner")
    fake_runner.make_runner = lambda: made.append(1) or (_ for _ in ()).throw(AssertionError("runner"))
    monkeypatch.setitem(sys.modules, "core.learn", fake_learn)
    monkeypatch.setitem(sys.modules, "core.runner", fake_runner)
    import core

    monkeypatch.setattr(core, "learn", fake_learn, raising=False)
    monkeypatch.setattr(core, "runner", fake_runner, raising=False)

    # isolated data dir with no baseline files
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "candidates.json").write_text(json.dumps(growth.DEFAULT_POOL))
    monkeypatch.setattr(growth, "DATA_DIR", tmp_path / "data")

    out = await growth.improve()
    assert made == []
    vetoed = [r for r in out["candidates"] if r["status"] == "vetoed_prelint"]
    poison_names = {p["name"] for p in growth.DEFAULT_POOL["poisons"]}
    poison_rows = [r for r in vetoed if r["name"] in poison_names]
    assert len(poison_rows) == 3
    assert all("forbidden pattern" in r["veto_reason"] for r in poison_rows)
    # the composed pair dies at the length ratio (reported); nothing else is vetoed
    assert {r["name"] for r in vetoed} - poison_names <= {"cite-every-claim+name-the-reading"}
    assert all(r["status"] == "clean_awaiting_baseline" for r in out["candidates"] if r not in vetoed)
    assert out["baseline"] == "awaiting_baseline"
    assert [r.type for r in ledger].count("veto") == len(vetoed)
    assert all(r.target_kind == "candidate" for r in ledger)
    assert len(lessons) == len(vetoed)
    assert sum("CREATE candidate" in s for s in fake_db.sql()) == len(out["candidates"])
    for r in out["candidates"]:
        assert set(r) >= {"name", "axis", "status", "veto_reason", "verdict", "mean", "spread",
                          "delta", "ratio", "sandbox_id", "runner"}
