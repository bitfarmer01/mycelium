"""Frozen held-out split: seeded shuffle of sorted question ids -> data/heldout.json, data/train.json, data/split_hash.txt."""
from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.contracts import CONFIG  # noqa: E402

DATA = ROOT / "data"


def make_split(questions: list[dict], n: int, seed: int) -> tuple[list[str], list[str]]:
    """Return (heldout_ids, train_ids): seeded shuffle of the sorted ids, first n held out. Order-independent."""
    ids = sorted({str(q["id"]) for q in questions})
    random.Random(seed).shuffle(ids)
    return ids[:n], ids[n:]


def load_questions(path: Path) -> list[dict]:
    """Accept either a bare list or {"questions": [...]}."""
    data = json.loads(Path(path).read_text())
    return data["questions"] if isinstance(data, dict) else data


def frozen_split(questions: list[dict], n: int, seed: int) -> tuple[list[str], list[str], str]:
    """Honour a human-supplied split (every question has split heldout|train) else fall back to make_split. Returns (heldout, train, origin)."""
    labels = {str(q["id"]): q.get("split") for q in questions}
    if labels and all(v in ("heldout", "train") for v in labels.values()):
        return (sorted(k for k, v in labels.items() if v == "heldout"),
                sorted(k for k, v in labels.items() if v == "train"), "file")
    h, t = make_split(questions, n, seed)
    return h, t, "make_split"


def split_hash(heldout: list[str]) -> str:
    return hashlib.sha256(",".join(heldout).encode()).hexdigest()


def main() -> int:
    src = DATA / "questions.json"
    if not src.exists():
        print("questions=0 NEED data/questions.json")
        return 1
    questions = load_questions(src)
    heldout, train, origin = frozen_split(questions, CONFIG["held_out_n"], CONFIG["seed"])
    h = split_hash(heldout)
    (DATA / "heldout.json").write_text(json.dumps(heldout, indent=2) + "\n")
    (DATA / "train.json").write_text(json.dumps(train, indent=2) + "\n")
    (DATA / "split_hash.txt").write_text(h + "\n")
    print(f"heldout={len(heldout)} train={len(train)} origin={origin} hash={h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
