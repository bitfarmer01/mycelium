"""Baseline: k_runs exam runs of the current slot over the held-out packs; writes data/baseline_runs.json."""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")  # values go to os.environ only; never printed

from core import db, runner  # noqa: E402
from core.contracts import CONFIG  # noqa: E402

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


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
