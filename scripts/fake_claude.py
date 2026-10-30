#!/usr/bin/env python3
"""Fake `claude` CLI for boots without claude/Bedrock credentials (demo + e2e tooling).

Mycelium's model gateway shells out to `claude --print --output-format json` with the
prompt on stdin and an optional --json-schema (core/llm.py). This shim emits the same
envelope: core.llm.parse_result() prefers the `structured_output` field, and
core.llm._usage_from_proc() folds the `usage` dict into token accounting (feature 23).

Call routing by the schema the caller sends (core/contracts.py):
- readings        (schema carries "flip_condition")  -> deterministic readings, intent-favored
- reflect         (schema carries "sentences")        -> schema-valid stub; core.reflect
                                                       re-validates row_ids against real
                                                       ledger rows and falls back if empty
- synthesis       (schema carries "spoken")           -> cites [1]; resolves whenever the
                                                       numbered context is non-empty
- exam scoring    (no schema; LocalRunner per-pack)   -> synthesis shape; core.growth
                                                       .score_answer reads claims/spoken/screen

The readings p-vector (intent 0.40, literal 0.30, scope 0.30) is deliberate: under a
uniform prior, intent wins select_reading; one correction at prior_rate_correct=0.3 moves
the shape prior enough for literal to win — the flip scripts/smoke.sh beat 3 needs.

Put it on PATH ahead of a real claude for a boot:  PATH="$PWD/scripts/fake:$PATH"
"""

from __future__ import annotations

import json
import sys

USAGE = {"input_tokens": 132, "output_tokens": 96, "cache_read_input_tokens": 0}


def _schema(argv: list[str]) -> dict:
    for i, a in enumerate(argv):
        if a == "--json-schema" and i + 1 < len(argv):
            try:
                return json.loads(argv[i + 1])
            except json.JSONDecodeError:
                return {}
    return {}


def _question(prompt: str) -> str:
    for line in (prompt or "").splitlines():
        line = line.strip()
        if line and not line.startswith(("{", "[", "CONTRACT", "##")):
            return line[:200]
    return (prompt or "").strip()[:200] or "the question"


def readings(prompt: str) -> dict:
    q = _question(prompt)
    return {
        "shape": "factual",
        "readings": [
            {
                "role": "literal",
                "paraphrase": q,
                "assumption": f"ask for exactly {q}",
                "query": q,
                "p": 0.30,
            },
            {
                "role": "intent",
                "paraphrase": q,
                "assumption": "the need behind the wording",
                "query": q,
                "p": 0.40,
            },
            {
                "role": "scope",
                "paraphrase": q,
                "assumption": "limit to the indexed wiki",
                "query": q,
                "p": 0.30,
            },
        ],
        "taken": "intent",
        "why": "shim: intent reading wins under a uniform prior",
        "flip_condition": "a correction moving the shape prior toward literal or scope",
    }


def synthesis() -> dict:
    spoken = "The wiki covers this in the cited document."
    return {
        "spoken": spoken,
        "screen": f"{spoken} [1]",
        "claims": [{"text": spoken, "chunk_ids": [1]}],
        "contradictions": [],
        "gaps": [],
        "confidence": "medium",
    }


def reflect() -> dict:
    return {
        "sentences": [{"text": "Learning summary from the demo shim.", "row_ids": []}]
    }


def main() -> int:
    argv = sys.argv[1:]
    prompt = sys.stdin.read() if not sys.stdin.isatty() else ""
    schema = _schema(argv)
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if "flip_condition" in props:
        out = readings(prompt)
    elif "sentences" in props:
        out = reflect()
    elif "spoken" in props or not props:  # no schema == exam scoring pack
        out = synthesis()
    json.dump(
        {"result": json.dumps(out), "structured_output": out, "usage": USAGE},
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
