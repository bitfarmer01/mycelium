# Mycelium

Mycelium answers company questions with citations, names the reading it took, and learns from corrections. A correction becomes a prior, so the next person asking a similar question gets the right reading first. It ranks what it knows by proven usefulness on a live feed, rewrites its own prompt when a sandboxed candidate beats the baseline above the run-to-run spread.

## How it works

Every question goes to one Claude Code call that returns two or more readings, the reading it took, a short spoken answer, a screen answer, and a claim list where each claim names the chunk ids that support it. Retrieval is one vector search over the chunk table for the taken reading, and the prompt carries a fixed contract of citation rules plus a mutable slot file. A correction writes an observation row and a ledger row, moves the prior for that question shape, and the live feed reorders without a refresh. When Improve yourself runs, three clean prompt suffixes and one composed pair are linted, run in Daytona containers against ten held-out questions, and scored against a baseline band, while a poisoned suffix is vetoed before any sandbox is created. Ask what it learned today and it selects the day's rows into one Claude call that returns sentences, each carrying a row id that resolves back to the ledger.

## Built with

- SurrealDB as the only database, holding the knowledge, the priors, the ledger and the live feed.
- ElevenLabs for speech in and out.
- Arize AX tracing of every Claude Code call.
- Daytona container sandboxes authenticating through Amazon Bedrock for the exam.
- Claude Code as the only model path.

## What we did not claim

- No retention or transfer claims.
- The gate is measured on 10 held-out questions.
- Sandboxes run on Bedrock, not the Max plan.

## README

Run steps:

```
docker compose -f infra/docker-compose.yml up -d
uv sync
```

Put source documents in `data/docs/` and the labelled questions in `data/questions.json`, then:

```
uv run python scripts/ingest.py
uv run python scripts/make_split.py
uv run uvicorn app.server:app --port 8787
```
