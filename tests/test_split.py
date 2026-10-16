"""Held-out split is disjoint, sized and stable."""
from scripts.make_split import make_split


def _qs(n=29):
    return [{"id": f"q{i:02d}", "text": f"question {i}"} for i in range(n)]


def test_split_is_disjoint_and_stable():
    heldout, train = make_split(_qs(), n=10, seed=42)
    assert len(heldout) == 10
    assert len(train) == 19
    assert not set(heldout) & set(train)
    h2, t2 = make_split(list(reversed(_qs())), n=10, seed=42)
    assert h2 == heldout and t2 == train


def test_frozen_split_honours_file_labels():
    from scripts.make_split import frozen_split

    qs = _qs()
    for i, q in enumerate(qs):
        q["split"] = "heldout" if i < 10 else "train"
    h, t, origin = frozen_split(qs, 10, 42)
    assert origin == "file" and h == [f"q{i:02d}" for i in range(10)] and len(t) == 19
    h2, _, origin2 = frozen_split(_qs(), 10, 42)
    assert origin2 == "make_split" and h2 == make_split(_qs(), 10, 42)[0]
