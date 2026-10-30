"""Rebuild the held-out split (and optionally its packs) from the corpus — deterministically (feature 48).

Same input -> same output: question ids are de-duplicated and sorted before the seeded
shuffle, every question's `split` label is honoured when the labels are complete
(scripts.make_split.frozen_split), and every output file is written byte-identically
across runs. Pack files are only rewritten with --packs, which re-runs retrieval
against the current corpus (needs the embedding model); without it the tool checks
that data/heldout_packs.json still covers the rebuilt held-out ids and refuses with a
non-zero exit on drift, instead of silently scoring a stale pack set.

usage: uv run python scripts/refresh_heldout.py [--packs] [--data-dir PATH]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.contracts import CONFIG
from scripts.make_split import frozen_split, load_questions, split_hash


def rebuild_split(questions: list[dict], data: Path) -> tuple[list[str], list[str], str, str]:
    """Rewrite heldout.json, train.json and split_hash.txt from the questions file. Returns (heldout, train, origin, hash)."""
    heldout, train, origin = frozen_split(questions, CONFIG["held_out_n"], CONFIG["seed"])
    h = split_hash(heldout)
    (data / "heldout.json").write_text(json.dumps(heldout, indent=2) + "\n")
    (data / "train.json").write_text(json.dumps(train, indent=2) + "\n")
    (data / "split_hash.txt").write_text(h + "\n")
    return heldout, train, origin, h


def pack_ids(data: Path) -> list[str] | None:
    """The question ids the current pack file covers, or None when it is missing or unreadable."""
    try:
        packs = json.loads((data / "heldout_packs.json").read_text())
        return [str(p["question_id"]) for p in packs]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def check_pack_coverage(data: Path, heldout: list[str]) -> tuple[bool, list[str], list[str]]:
    """(ok, stale, missing): do the existing packs cover exactly the rebuilt held-out ids?"""
    ids = pack_ids(data)
    if ids is None:
        return False, [], list(heldout)
    have = set(ids)
    want = set(heldout)
    return have == want, sorted(have - want), sorted(want - have)


async def rebuild_packs(data: Path, heldout: list[str]) -> None:
    """Re-run retrieval for each held-out id and rewrite heldout_packs.json (needs the embedding model)."""
    from core import retrieve

    questions = {str(q["id"]): q for q in load_questions(data / "questions.json")}
    packs = []
    for qid in heldout:  # sorted by rebuild_split -> deterministic pack order
        text = questions[qid]["text"]
        context, citations, _rows = await retrieve.retrieve(text)
        packs.append(retrieve.evidence_pack(qid, text, context, citations))
    (data / "heldout_packs.json").write_text(json.dumps(packs, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild the held-out split deterministically (feature 48)")
    ap.add_argument("--packs", action="store_true",
                    help="also rebuild heldout_packs.json via retrieval (loads the embedding model)")
    ap.add_argument("--data-dir", default=str(ROOT / "data"), help="data directory (default: repo data/)")
    args = ap.parse_args(argv)
    data = Path(args.data_dir)

    src = data / "questions.json"
    if not src.exists():
        print(f"NEED {src}")
        return 1
    heldout, train, origin, h = rebuild_split(load_questions(src), data)
    print(f"heldout={len(heldout)} train={len(train)} origin={origin} hash={h}")

    if args.packs:
        asyncio.run(rebuild_packs(data, heldout))
        print(f"packs={len(heldout)} rewritten")
        return 0

    ok, stale, missing = check_pack_coverage(data, heldout)
    if ok:
        print("packs=ok cover the rebuilt held-out ids")
        return 0
    print(f"packs=STALE need --packs to rebuild (stale={stale} missing={missing})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
