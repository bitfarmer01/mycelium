"""Feature 48: scripts/refresh_heldout.py rebuilds the split deterministically (same input -> same output)."""
from __future__ import annotations

import json

from scripts.refresh_heldout import check_pack_coverage, main, rebuild_split


def _questions(n=29, labeled=False):
    qs = [{"id": f"q{i:02d}", "text": f"question {i}"} for i in range(n)]
    if labeled:
        for i, q in enumerate(qs):
            q["split"] = "heldout" if i < 10 else "train"
    return qs


def write_questions(tmp_path, questions):
    (tmp_path / "questions.json").write_text(json.dumps({"questions": questions}))


def read_all(tmp_path):
    return {name: (tmp_path / name).read_text()
            for name in ("heldout.json", "train.json", "split_hash.txt")}


def test_rebuild_is_deterministic(tmp_path):
    write_questions(tmp_path, _questions())
    first = rebuild_split(_questions(), tmp_path)
    one = read_all(tmp_path)
    second = rebuild_split(list(reversed(_questions())), tmp_path)  # input order must not matter
    two = read_all(tmp_path)
    assert one == two, "same input -> byte-identical output"
    assert first[3] == second[3] and (tmp_path / "split_hash.txt").read_text().strip() == first[3]
    heldout = json.loads((tmp_path / "heldout.json").read_text())
    train = json.loads((tmp_path / "train.json").read_text())
    assert len(heldout) == 10 and len(train) == 19
    assert not set(heldout) & set(train)


def test_rebuild_honours_file_labels(tmp_path):
    qs = _questions(labeled=True)
    write_questions(tmp_path, qs)
    heldout, _train, origin, _h = rebuild_split(qs, tmp_path)
    assert origin == "file"
    assert heldout == [f"q{i:02d}" for i in range(10)]


def test_main_refuses_stale_packs(tmp_path, capsys):
    write_questions(tmp_path, _questions())
    heldout, _, _, _ = rebuild_split(_questions(), tmp_path)
    (tmp_path / "heldout_packs.json").write_text(json.dumps(
        [{"question_id": "zz", "text": "stale", "context": "c", "citations": []}]))

    rc = main(["--data-dir", str(tmp_path)])
    assert rc == 1, "drifted packs must fail loudly, not silently score a stale set"
    assert "STALE" in capsys.readouterr().out
    ok, stale, missing = check_pack_coverage(tmp_path, heldout)
    assert not ok and stale == ["zz"] and len(missing) == 10


def test_main_accepts_covering_packs(tmp_path, capsys):
    write_questions(tmp_path, _questions())
    heldout, _, _, _ = rebuild_split(_questions(), tmp_path)
    (tmp_path / "heldout_packs.json").write_text(json.dumps(
        [{"question_id": qid, "text": "t", "context": "c", "citations": []} for qid in heldout]))

    rc = main(["--data-dir", str(tmp_path)])
    assert rc == 0
    assert "packs=ok" in capsys.readouterr().out


def test_main_needs_questions_file(tmp_path, capsys):
    rc = main(["--data-dir", str(tmp_path)])
    assert rc == 1
    assert "NEED" in capsys.readouterr().out
