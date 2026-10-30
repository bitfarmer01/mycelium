"""A/B harness CLI: score two slot variants over the held-out packs, emit a winner report.

Report-only: the harness never writes prompts/slot.md. A winner reaches production only through an
explicit growth.promote() (the /improve gate or an operator).

Examples:
  python scripts/ab_harness.py --slot-b prompts/candidates/my-slot.md
  python scripts/ab_harness.py --slot-b prompts/slot.md --runs 1 --tol 0.05
      # with MYCELIUM_CHEAP_MODEL set, this is the feature-27 tolerance check: the current slot
      # scored fresh under routing, against the stored all-Opus baseline runs
  python scripts/ab_harness.py --slot-b prompts/slot.md --baseline data/baseline_runs.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")  # values go to os.environ only; never printed

from core import ab, runner

DATA = ROOT / "data"


async def main() -> int:
    ap = argparse.ArgumentParser(description="Score two slot variants on the eval split; report only, never promotes.")
    ap.add_argument("--slot-a", default="prompts/slot.md", help="variant A slot file (ignored with --baseline)")
    ap.add_argument("--slot-b", required=True, help="variant B slot file")
    ap.add_argument("--baseline", default="", help="optional JSON of stored A runs (e.g. data/baseline_runs.json); skips the A run")
    ap.add_argument("--packs", default=str(DATA / "heldout_packs.json"))
    ap.add_argument("--terms", default=str(DATA / "terms.json"))
    ap.add_argument("--out", default=str(DATA / "ab_reports"))
    ap.add_argument("--runs", type=int, default=1, help="fresh runs per variant (default 1)")
    ap.add_argument("--tol", type=float, default=0.0, help="tolerance for 'B within tolerance of A' (default 0: strict no-regression)")
    args = ap.parse_args()

    from core import growth

    slot_a = growth.load_slot(args.slot_a)
    slot_b = growth.load_slot(args.slot_b)
    packs = json.loads(Path(args.packs).read_text())
    terms_p = Path(args.terms)
    terms = json.loads(terms_p.read_text()) if terms_p.exists() else {}

    baseline_runs = None
    if args.baseline:
        stored = json.loads(Path(args.baseline).read_text())
        baseline_runs = stored.get("runs") if isinstance(stored, dict) else stored
        if not baseline_runs:
            print(f"ab_harness: {args.baseline} carries no runs", file=sys.stderr)
            return 2
        name_a = f"baseline:{stored.get('runner', 'stored')}" if isinstance(stored, dict) else "baseline:stored"
    else:
        name_a = Path(args.slot_a).stem
    name_b = Path(args.slot_b).stem

    report = await ab.run_ab(slot_a, slot_b, packs, terms, runner.make_runner(), runs=args.runs,
                             name_a=name_a, name_b=name_b, tol=args.tol, baseline_runs=baseline_runs)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ab-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"A/B report: {out_path}")
    print(f"winner: {report['winner']}  (A {report['a']['mean']:.3f} vs B {report['b']['mean']:.3f}, "
          f"delta {report['delta']:+.3f}, within tolerance: {report['within_tolerance']})")
    print("the production slot was not changed; promotion is an explicit growth.promote()")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
