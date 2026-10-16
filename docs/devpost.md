# Mycelium

Mycelium answers company questions with citations, names the reading it took, and learns from corrections. A correction becomes a prior, so the next person asking a question gets the right reading first. It ranks what it knows by proven usefulness on a live feed, and rewrites its own instructions only when a sandboxed candidate beats the baseline above the spread.

## How it works

Every question goes to one Claude Code call that returns two or more readings, the reading it took, a short spoken answer, a screen answer, and a claim list where each claim names the chunk ids that support it. The corpus is SurrealDB's own documentation, chunked and embedded locally, and judges can drop their own PDFs, spreadsheets, markdown, text or HTML into the upload zone, where each file is chunked, embedded and searchable at once; retrieval for the taken reading is one vector search over the chunk table, and the prompt carries a fixed contract of citation rules plus a mutable slot file. A correction writes an observation row and a ledger row, moves the prior for that question shape, and the live feed reorders without a refresh, so every learning event visibly moves a value in the console. When Improve yourself runs, three clean prompt suffixes and one composed pair are linted, run in Daytona containers against ten held-out questions, and scored against a baseline band, while a poisoned suffix is vetoed before any sandbox is created. Ask what it learned today and it selects the day's rows into one Claude call that returns sentences, each carrying a row id that resolves back to the ledger.

## Built with

- SurrealDB as the only database, holding the knowledge, the priors, the ledger, the live feed and the corpus chunks.
- A local loader that reads markdown, text, HTML, PDF, CSV, TSV and XLSX into SurrealDB, with bge-large-en-v1.5 embeddings (1024-d) for the corpus and the queries.
- ElevenLabs for speech in and out, over the same endpoints as typed input.
- Arize AX tracing of every Claude Code call.
- Daytona container sandboxes authenticating through Amazon Bedrock for the exam.
- Claude Code as the only model path.

## What we did not claim

- No retention or transfer claims.
- The gate is measured on 10 held-out questions with a deterministic must-mention scorer.
- The numerical gate is designed, measured after the demo: data/baseline_runs.json is not in the repo.
- Sandboxed Claude Code runs on Bedrock, not the Max plan.

## Try it yourself

Judges can upload their own documents in the page and ask about them at once. Up to 10 files, 20 MB each, 50 MB per upload, 800 chunks per upload, one upload at a time. See `docs/judges.md` for the walkthrough.
