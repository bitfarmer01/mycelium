"""SurrealDB-only corpus path: data/docs/**/*.{md,txt,html} -> chunk + source rows (bge-large-en-v1.5, 1024-d); data/questions.json -> question rows.

Idempotent: chunk ids are sha1(abs path + index), source ids sha1(abs path), question ids from the file.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import html as _html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import db  # noqa: E402
from core.contracts import CONFIG  # noqa: E402

DOCS = ROOT / "data" / "docs"
QUESTIONS = ROOT / "data" / "questions.json"
MODEL = "BAAI/bge-large-en-v1.5"
EXTS = {".md", ".txt", ".html", ".htm"}
EMBED_BATCH = 32
INSERT_BATCH = 200


# ---------- pure helpers (unit-tested) ----------

def chunk_text(text: str, words: int = 350, overlap: int = 50) -> list[str]:
    """Split on paragraph boundaries into chunks of <= `words` words; consecutive chunks share exactly `overlap` words.

    A chunk ends at the last paragraph boundary that fits; if none fits (huge paragraph) it hard-cuts at `words`.
    """
    paras = [p.split() for p in re.split(r"\n\s*\n", text) if p.split()]
    toks: list[str] = []
    bounds: set[int] = set()  # token index where a new paragraph starts
    for p in paras:
        if toks:
            bounds.add(len(toks))
        toks.extend(p)
    total = len(toks)
    if total == 0:
        return []
    out: list[str] = []
    start = 0
    while True:
        limit = min(start + words, total)
        end = limit
        if limit < total:
            cands = [b for b in bounds if start + overlap < b <= limit]
            if cands:
                end = max(cands)
        parts, cur = [], [toks[start]]
        for i in range(start + 1, end):
            if i in bounds:
                parts.append(" ".join(cur))
                cur = []
            cur.append(toks[i])
        parts.append(" ".join(cur))
        out.append("\n\n".join(parts))
        if end >= total:
            return out
        start = end - overlap


def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = _html.unescape(raw)
    return re.sub(r"[ \t]+", " ", raw)


def extract_title(text: str, filename: str) -> str:
    m = re.search(r"(?m)^#\s+(.+?)\s*#*\s*$", text)
    return m.group(1).strip() if m else Path(filename).stem


def html_title(raw: str) -> str | None:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw) or re.search(r"(?is)<h1[^>]*>(.*?)</h1>", raw)
    return _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() or None if m else None


def front_matter_link(text: str) -> tuple[str | None, str]:
    """Return (source_link, text_without_that_line) when a `source_link:` line is present near the top."""
    m = re.search(r"(?mi)^\s*source_link:\s*(\S+)\s*$", text[:2000])
    if not m:
        return None, text
    return m.group(1), text[: m.start()] + text[m.end():]


def chunk_id(path: str, index: int) -> str:
    return hashlib.sha1(f"{path}#{index}".encode()).hexdigest()[:20]


def source_id(path: str) -> str:
    return hashlib.sha1(path.encode()).hexdigest()[:20]


def load_doc(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8", errors="replace")
    link, raw = front_matter_link(raw)
    if path.suffix.lower() in (".html", ".htm"):
        title = html_title(raw) or path.stem
        text = strip_html(raw)
    else:
        text = re.sub(r"(?s)\A---\n.*?\n---\n", "", raw)  # drop YAML front matter
        title = extract_title(text, path.name)
    ts = _dt.datetime.fromtimestamp(path.stat().st_mtime, _dt.timezone.utc).isoformat()
    ap = str(path.resolve())
    return {"path": ap, "title": title, "text": text, "link": link or f"file://{ap}", "timestamp": ts}


def doc_files(root: Path = DOCS) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTS)


# ---------- embedding + DB ----------

def embed(texts: list[str]) -> list[list[float]]:
    from sentence_transformers import SentenceTransformer  # lazy: unit tests never load the model

    model = SentenceTransformer(MODEL)
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        vecs = model.encode(texts[i:i + EMBED_BATCH], normalize_embeddings=True)
        out.extend([float(x) for x in v] for v in vecs)
    return out


async def write_chunks(rows: list[dict]) -> None:
    sql = (
        "INSERT INTO chunk $rows ON DUPLICATE KEY UPDATE "
        "document = $input.document, title = $input.title, source_link = $input.source_link, "
        "metadata = $input.metadata, timestamp = $input.timestamp, embedding = $input.embedding;"
    )
    for i in range(0, len(rows), INSERT_BATCH):
        await db.q(sql, {"rows": rows[i:i + INSERT_BATCH]})


async def ingest_docs() -> tuple[int, int]:
    files = doc_files()
    chunks: list[dict] = []
    sources: list[dict] = []
    for f in files:
        d = load_doc(f)
        sid = source_id(d["path"])
        sources.append({"id": sid, "title": d["title"]})
        for i, c in enumerate(chunk_text(d["text"])):
            chunks.append({
                "id": chunk_id(d["path"], i),
                "document": c,
                "title": d["title"],
                "source_link": d["link"],
                "metadata": {"path": d["path"], "index": i, "source": f"source:{sid}", "words": len(c.split())},
                "timestamp": d["timestamp"],
                "alpha": 1, "beta": 1, "cited_count": 0, "retrieved_count": 0, "last_event": None,
            })
    if chunks:
        vecs = embed([c["document"] for c in chunks])
        for c, v in zip(chunks, vecs):
            c["embedding"] = v
        await write_chunks(chunks)
    for s in sources:
        await db.q(
            "UPSERT type::record('source', $id) MERGE { title: $title }; "
            "UPDATE type::record('source', $id) SET alpha = alpha ?? 1, beta = beta ?? 1;",
            s,
        )
    return len(chunks), len(sources)


async def ingest_questions() -> int:
    if not QUESTIONS.exists():
        print("questions=0 NEED data/questions.json")
        return 0
    from scripts.make_split import frozen_split, load_questions

    questions = load_questions(QUESTIONS)
    heldout, _, _ = frozen_split(questions, CONFIG["held_out_n"], CONFIG["seed"])
    held = set(heldout)
    for qd in questions:
        qid = str(qd["id"])
        row = {
            "text": qd.get("text") or qd.get("question", ""),
            "answer": qd.get("answer"),
            "shape": qd.get("shape"),
            "session_id": qd.get("session_id"),
            "readings": qd.get("readings", []),
            "taken": qd.get("taken"),
            "split": "heldout" if qid in held else "train",
            "ts": qd.get("ts") or db.now(),
        }
        await db.q("UPSERT type::record('question', $id) MERGE $row;", {"id": qid, "row": row})
    return len(questions)


async def main() -> None:
    n_chunk, n_source = await ingest_docs()
    if n_chunk == 0:
        print(f"NEED documents in {DOCS} (md, txt, html)")
    n_q = await ingest_questions()
    print(f"chunk={n_chunk} source={n_source} question={n_q}")


if __name__ == "__main__":
    asyncio.run(main())
