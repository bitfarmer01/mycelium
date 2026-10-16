# mycelium

Mycelium answers company questions with citations, names the reading it took, and learns from corrections. The corpus is SurrealDB's own documentation. The loader reads markdown, text, HTML, PDF, CSV, TSV and XLSX, and judges can upload their own documents in the page and ask about them at once.

Run steps:

1. docker compose -f infra/docker-compose.yml up -d
2. uv sync
3. uv run python scripts/ingest.py
4. uv run uvicorn app.server:app --port 8787
5. Open http://localhost:8787 and drag files onto the upload zone.
