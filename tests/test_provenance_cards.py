"""Provenance card render path (feature 62): per-claim cards show citation, cosine and
fitness only when the answer payload carries each field — absent fields stay hidden.
Static checks on the console modules (no browser needed); interaction evidence is
captured via agent-browser dogfooding against the tested head SHA."""

from __future__ import annotations

import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _js(name: str) -> str:
    return (JS_DIR / name).read_text(encoding="utf-8")


def test_citation_card_renders_fitness_conditionally() -> None:
    src = _js("answer.js")
    assert re.search(r'c\.fitness\s*!=\s*null.*"fitness "', src, re.DOTALL), (
        "answer.js must render the fitness score only when the citation payload "
        "carries it (field-level degrade, feature 62)"
    )


def test_citation_card_renders_cosine_conditionally() -> None:
    src = _js("answer.js")
    assert re.search(r'c\.cosine\s*!=\s*null.*"cosine "', src, re.DOTALL), (
        "answer.js must render the cosine score only when the citation payload "
        "carries it (field-level degrade, feature 62)"
    )


def test_mock_fixture_carries_cosine_and_fitness() -> None:
    """Mock mode must exercise the full provenance path (citation + cosine + fitness)."""
    src = _js("fixtures.js")
    m = re.search(r"askCited:\s*\{.*?\n  \}", src, re.DOTALL)
    assert m, "fixtures.js must define the askCited fixture"
    block = m.group(0)
    for field in ("cosine:", "fitness:", "chunk_id:", "link:", "snippet:"):
        assert field in block, f"askCited fixture citations missing {field}"
