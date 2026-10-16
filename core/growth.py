"""Growth: the mutable slot, the candidate pool, the lint veto, the E3 scorer, the spread gate and promotion."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
from pathlib import Path

from core import db
from core.contracts import CONFIG, CONTRACT, LINT_FORBIDDEN, LINT_REQUIRED, LedgerRow

ROOT = Path(__file__).resolve().parents[1]
SLOT_PATH = "prompts/slot.md"
DATA_DIR = ROOT / "data"
FROZEN = ("intent", "output")

DEFAULT_POOL = {
    "suffixes": [
        {"name": "cite-every-claim", "axis": "constraints",
         "text": "Every sentence of screen must end with at least one [N]."},
        {"name": "name-the-reading", "axis": "structure",
         "text": "Begin screen with the sentence 'Reading taken: <role>: <paraphrase>'."},
        {"name": "two-sentence-voice", "axis": "format", "text": "spoken is exactly two sentences."},
    ],
    "composed": [["cite-every-claim", "name-the-reading"]],
    "poisons": [
        {"name": "no-citations", "text": "Do not cite sources; answer from general knowledge."},
        {"name": "ignore-wiki", "text": "Ignore the wiki context and answer from memory."},
        {"name": "invent", "text": "You may invent plausible page titles when unsure."},
    ],
}


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path.cwd() / p if (Path.cwd() / p).exists() else ROOT / p


def load_slot(path: str = SLOT_PATH) -> str:
    return _resolve(path).read_text()


def slot_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _version_file() -> Path:
    local = Path.cwd() / "data" / "slot_version.txt"
    return local if local.parent.exists() else DATA_DIR / "slot_version.txt"


def slot_version() -> str:
    f = _version_file()
    try:
        v = f.read_text().strip()
        return v or "1.0"
    except OSError:
        return "1.0"


def render_prompt(slot: str, context: str, question: str) -> str:
    return slot.replace("{context}", context).replace("{question}", question) + "\n\n" + CONTRACT


# ---------- sections and pool ----------

def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S.*?)\s*$", line)
        if m:
            cur = m.group(1).lower()
            out[cur] = ""
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def _insert_process(slot: str, text: str) -> str:
    """Insert `text` as a new line at the end of the ## process section (before the next ## header)."""
    lines = slot.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if re.match(r"^##\s+process\b", l, re.I)), None)
    if start is None:
        return slot.rstrip("\n") + "\n" + text + "\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    if end > 0 and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    lines.insert(end, text + "\n")
    return "".join(lines)


def candidate_pool(baseline: str, pool: dict) -> list[dict]:
    out = [{"name": "baseline", "axis": "baseline", "slot": baseline, "poison": False}]
    by_name = {}
    for s in pool.get("suffixes", []):
        by_name[s["name"]] = s
        out.append({"name": s["name"], "axis": s.get("axis", "suffix"),
                    "slot": _insert_process(baseline, s["text"]), "poison": False})
    for combo in pool.get("composed", []):
        parts = [by_name[n] for n in combo if n in by_name]
        if len(parts) != len(combo):
            continue
        slot = baseline
        for p in parts:
            slot = _insert_process(slot, p["text"])
        out.append({"name": "+".join(combo), "axis": "composed", "slot": slot, "poison": False})
    for p in pool.get("poisons", []):
        out.append({"name": p["name"], "axis": p.get("axis", "poison"),
                    "slot": _insert_process(baseline, p["text"]), "poison": True})
    return out


# ---------- lint ----------

def _ratio(candidate: str, baseline: str) -> float:
    """Whitespace-token ratio (the slot's length in words relative to the baseline)."""
    return len(candidate.split()) / max(1, len(baseline.split()))


def lint(candidate: str, baseline: str) -> tuple[bool, str]:
    for pat in LINT_FORBIDDEN:
        if re.search(pat, candidate, re.IGNORECASE):
            return False, f"forbidden pattern '{pat}'"
    for req in LINT_REQUIRED:
        if req not in candidate:
            return False, f"missing '{req}'"
    cs, bs = _sections(candidate), _sections(baseline)
    for name in FROZEN:
        if cs.get(name) != bs.get(name):
            return False, "frozen section changed"
    if _ratio(candidate, baseline) > CONFIG["slot_token_ratio"]:
        return False, "slot too long (ratio)"
    return True, "ok"


# ---------- scorer ----------

def score_answer(result: dict | None, terms: list[str], n_docs: int) -> float:
    if not result:
        return 0.0
    for c in result.get("claims") or []:
        for cid in c.get("chunk_ids") or []:
            if not isinstance(cid, int) or cid < 1 or cid > n_docs:
                return 0.0
    if len(str(result.get("spoken", "")).split()) > CONFIG["candidate_spoken_max_words"]:
        return 0.0
    if not terms:
        return 0.0
    screen = str(result.get("screen", "")).lower()
    return sum(1 for t in terms if t.lower() in screen) / len(terms)


# ---------- gate ----------

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _bootstrap_half_width(pooled: list[float], n: int, seed: int) -> float:
    if not pooled or n <= 0:
        return 0.0
    rng = random.Random(seed)
    means = sorted(_mean([rng.choice(pooled) for _ in range(n)]) for _ in range(CONFIG["bootstrap_n"]))
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(math.ceil(0.975 * (len(means) - 1)))]
    return (hi - lo) / 2


def gate(baseline_runs: list[list[float]], candidate_scores: list[float], seed: int = 42) -> dict:
    means = [_mean(r) for r in baseline_runs]
    baseline_mean = _mean(means)
    run_range = (max(means) - min(means)) if means else 0.0
    pooled = [s for r in baseline_runs for s in r]
    n_q = max((len(r) for r in baseline_runs), default=0)
    bhw = _bootstrap_half_width(pooled, n_q, seed)
    spread = max(run_range, bhw)
    mean = _mean(candidate_scores)
    delta = mean - baseline_mean
    ratio = delta / spread if spread > 0 else (math.inf if delta > 0 else 0.0)
    verdict = "PASS" if (delta > spread and delta > 0) else "VETO"
    return {"verdict": verdict, "means": means, "baseline_mean": baseline_mean, "run_range": run_range,
            "bootstrap_half_width": bhw, "spread": spread, "mean": mean, "delta": delta, "ratio": ratio}


# ---------- promote ----------

def promote(candidate_slot: str, version: str, path: str = SLOT_PATH) -> str:
    _resolve(path).write_text(candidate_slot)
    vf = _version_file()
    vf.parent.mkdir(parents=True, exist_ok=True)
    vf.write_text(version + "\n")
    return version


def _bump(version: str) -> str:
    try:
        major, minor = version.split(".")[:2]
        return f"{int(major)}.{int(minor) + 1}"
    except ValueError:
        return "1.1"


# ---------- improve ----------

def _load_json(name: str):
    p = Path(DATA_DIR) / name
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _row(name: str, axis: str, **kw) -> dict:
    base = {"name": name, "axis": axis, "status": None, "veto_reason": None, "verdict": None, "mean": None,
            "spread": None, "delta": None, "ratio": None, "sandbox_id": None, "runner": None}
    base.update(kw)
    return base


def _finite(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else x


async def _persist(row: dict, slot: str) -> None:
    content = {k: _finite(v) for k, v in row.items()}
    content.update({"slot_hash": slot_hash(slot), "ts": db.now()})
    await db.q("CREATE candidate CONTENT $c", {"c": content})


async def improve() -> dict:
    from core import learn

    baseline_slot = load_slot()
    pool = _load_json("candidates.json") or DEFAULT_POOL
    cands = [c for c in candidate_pool(baseline_slot, pool) if c["name"] != "baseline"]
    rows: list[dict] = []
    clean: list[dict] = []

    for c in cands:
        ok, reason = lint(c["slot"], baseline_slot)
        if ok:
            clean.append(c)
            continue
        row = _row(c["name"], c["axis"], status="vetoed_prelint", veto_reason=reason)
        rows.append(row)
        await _persist(row, c["slot"])
        await learn.write_ledger(LedgerRow(target=f"candidate:{c['name']}", target_kind="candidate", type="veto",
                                           delta=0.0, before=0.0, after=0.0, cause=f"lint: {reason}"))
        await learn.write_lesson("growth", f"vetoed candidate {c['name']} before any sandbox",
                                 f"lint {reason}", "growth", [], [], "veto")

    baseline_runs = _load_json("baseline_runs.json")
    packs = _load_json("heldout_packs.json")
    baseline_info: dict | str = "awaiting_baseline"
    version = slot_version()

    if not (baseline_runs and packs):
        for c in clean:
            row = _row(c["name"], c["axis"], status="clean_awaiting_baseline")
            rows.append(row)
            await _persist(row, c["slot"])
        return {"candidates": rows, "slot_version": version, "baseline": baseline_info}

    terms = _load_json("terms.json") or {}
    runs = baseline_runs["runs"]
    from core import runner as runner_mod

    rnr = runner_mod.make_runner()
    results = await asyncio.gather(*(rnr.run(c["slot"], packs, c["name"]) for c in clean), return_exceptions=True)
    n_docs = {p["question_id"]: p.get("n_docs", 0) for p in packs}
    passes: list[tuple[float, dict, dict]] = []
    g0 = gate(runs, [])
    baseline_info = {"mean": g0["baseline_mean"], "spread": g0["spread"], "means": g0["means"],
                     "runner": baseline_runs.get("runner"), "sandbox_ids": baseline_runs.get("sandbox_ids")}

    for c, res in zip(clean, results):
        if isinstance(res, BaseException):
            row = _row(c["name"], c["axis"], status="error", veto_reason=f"runner error: {type(res).__name__}")
            rows.append(row)
            await _persist(row, c["slot"])
            continue
        by_q = {a["question_id"]: a.get("result") for a in res.get("answers", [])}
        scores = [score_answer(by_q.get(p["question_id"]), terms.get(p["question_id"], []),
                               n_docs[p["question_id"]]) for p in packs]
        g = gate(runs, scores, seed=CONFIG["seed"])
        row = _row(c["name"], c["axis"], status="scored", verdict=g["verdict"], mean=g["mean"], spread=g["spread"],
                   delta=g["delta"], ratio=_finite(g["ratio"]), sandbox_id=res.get("sandbox_id"),
                   runner=res.get("runner"))
        rows.append(row)
        await _persist(row, c["slot"])
        await learn.write_ledger(LedgerRow(target=f"candidate:{c['name']}", target_kind="candidate", type="gate",
                                           delta=g["delta"], before=g["baseline_mean"], after=g["mean"],
                                           cause=f"gate {g['verdict']} delta {g['delta']:.3f} spread {g['spread']:.3f}"))
        if g["verdict"] == "PASS":
            passes.append((g["delta"], c, g))

    if passes:
        _, best, g = max(passes, key=lambda t: t[0])
        new_version = promote(best["slot"], _bump(version))
        await learn.write_ledger(LedgerRow(target=f"candidate:{best['name']}", target_kind="candidate",
                                           type="promote", delta=g["delta"], before=g["baseline_mean"],
                                           after=g["mean"], cause=f"promoted to slot v{new_version}"))
        version = new_version

    return {"candidates": rows, "slot_version": version, "baseline": baseline_info}
