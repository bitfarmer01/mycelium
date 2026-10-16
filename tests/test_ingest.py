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


def _tiny_pdf(pages: list[str]) -> bytes:
    """Hand-built PDF with one Helvetica text line per page (no PDF writer dependency)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{n} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_load_pdf_one_part_per_page(tmp_path):
    from scripts.ingest import load_doc

    f = tmp_path / "security_policy.pdf"
    f.write_bytes(_tiny_pdf(["Report vulnerabilities to the security team", "Patches ship within 30 days"]))
    d = load_doc(f, {"security_policy.pdf": "https://example.org/policy.pdf"})
    assert d["kind"] == "pdf" and d["link"] == "https://example.org/policy.pdf"
    assert [p["meta"]["page"] for p in d["parts"]] == [1, 2]
    assert "security team" in d["parts"][0]["text"] and d["parts"][1]["label"] == "p. 2"


def test_load_csv_and_xlsx_rows_are_labelled(tmp_path):
    from openpyxl import Workbook

    from scripts.ingest import load_doc, rows_text

    c = tmp_path / "releases.csv"
    c.write_text("version,date,owner\n3.0.0,2026-09-01,core team\n2.2.1,2026-06-10,\n")
    d = load_doc(c)
    assert d["kind"] == "csv" and d["link"].startswith("file://")
    assert "version: 3.0.0; date: 2026-09-01; owner: core team" in d["parts"][0]["text"]
    assert "version: 2.2.1; date: 2026-06-10" in d["parts"][0]["text"]  # empty cells dropped

    x = tmp_path / "pricing.xlsx"
    wb = Workbook()
    wb.active.title = "Plans"
    wb.active.append(["plan", "price"])
    wb.active.append(["Start", 0])
    wb.create_sheet("Limits").append(["limit", "value"])
    wb.save(x)
    d = load_doc(x)
    assert [p["label"] for p in d["parts"]] == ["Plans", "Limits"]
    assert "plan: Start; price: 0" in d["parts"][0]["text"]
    assert d["parts"][1]["text"] == ""  # header-only sheet yields no rows
    assert rows_text([["a"], ["1"]] + [["2"]] * 25).count("\n\n") == 1  # paragraph break every 20 rows
