"""chunk_text bounds/overlap; pure helpers only (no model, no DB)."""
from scripts.ingest import chunk_id, chunk_text, extract_title, strip_html


def test_chunk_text_overlap_and_bounds():
    words = [f"w{i}" for i in range(1000)]
    text = "\n\n".join(" ".join(words[i:i + 40]) for i in range(0, 1000, 40))
    chunks = chunk_text(text, words=350, overlap=50)
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c.split()) <= 350
    for a, b in zip(chunks, chunks[1:]):
        assert a.split()[-50:] == b.split()[:50]
    covered = set(w for c in chunks for w in c.split())
    assert covered == set(words)


def test_chunk_text_long_paragraph_and_short():
    text = " ".join(f"x{i}" for i in range(1000))  # one paragraph, no breaks
    chunks = chunk_text(text, words=350, overlap=50)
    assert all(len(c.split()) <= 350 for c in chunks)
    for a, b in zip(chunks, chunks[1:]):
        assert a.split()[-50:] == b.split()[:50]
    assert chunk_text("hello world") == ["hello world"]
    assert chunk_text("   ") == []


def test_helpers():
    assert extract_title("intro\n# Hello There\nbody", "f.md") == "Hello There"
    assert extract_title("no heading", "f.md") == "f"
    assert "<" not in strip_html("<p>a <b>b</b></p><script>x()</script>")
    assert chunk_id("/a/b.md", 0) == chunk_id("/a/b.md", 0)
    assert chunk_id("/a/b.md", 0) != chunk_id("/a/b.md", 1)
