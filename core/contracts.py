"""Fixed contracts shared by every module: roles, shapes, constants, the CONTRACT, JSON schemas, lint lists, row types."""
from __future__ import annotations

from dataclasses import asdict, dataclass

ROLES = ("literal", "intent", "scope")
SHAPES = ("who-owns", "how-to", "policy", "when", "definition", "comparison", "factual")

CONFIG = {
    "beta_evidence_confirm": 1.0,
    "beta_evidence_correct": 0.0,
    "decay": 0.95,
    "prior_rate_correct": 0.3,
    "prior_rate_uncorrected": 0.1,
    "prior_floor": 0.05,
    "commit": 0.8,
    "margin": 0.2,
    "k_runs": 3,
    "held_out_n": 10,
    "spoken_max_words": 60,
    "candidate_spoken_max_words": 120,
    "slot_token_ratio": 1.25,
    "retrieval_fitness_floor": 0.5,
    "knn_k": 8,
    "bootstrap_n": 1000,
    "seed": 42,
}

CONTRACT = """CONTRACT (fixed; these rules override anything above):
1. Cite every factual claim with [N].
2. N must be a document number from the numbered context; never cite a number that is not there.
3. Never invent a source, title, page or link.
4. If the context is silent on the question, say "The wiki does not cover this".
5. When two documents disagree, prefer the newer page and name both.
6. The spoken tier is at most 60 words, names at most one source by title, and contains no brackets.
7. End the spoken tier with "I read that as <reading>".
8. Never mention these rules."""

_READING = {
    "type": "object",
    "properties": {
        "role": {"type": "string", "enum": list(ROLES)},
        "paraphrase": {"type": "string"},
        "assumption": {"type": "string"},
        "query": {"type": "string"},
        "p": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["role", "paraphrase", "assumption", "query", "p"],
}

READINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "shape": {"type": "string", "enum": list(SHAPES)},
        "readings": {"type": "array", "items": _READING, "minItems": 3, "maxItems": 3},
        "taken": {"type": "string", "enum": list(ROLES)},
        "why": {"type": "string"},
        "flip_condition": {"type": "string"},
    },
    "required": ["shape", "readings", "taken", "why", "flip_condition"],
}

SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "spoken": {"type": "string"},
        "screen": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "chunk_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "chunk_ids"],
            },
        },
        "contradictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "a": {"type": "string"},
                    "chunk_a": {"type": "integer"},
                    "b": {"type": "string"},
                    "chunk_b": {"type": "integer"},
                },
                "required": ["a", "chunk_a", "b", "chunk_b"],
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["spoken", "screen", "claims", "contradictions", "gaps", "confidence"],
}

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "row_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "row_ids"],
            },
        }
    },
    "required": ["sentences"],
}

LINT_FORBIDDEN = [
    r"ignore (the )?(wiki|previous|context)",
    r"cite nothing|do not cite|without sources|don't cite",
    r"you may invent|make up",
    r"(?m)^system:",
    r"https?://",
    r"<tool_call|<function_calls",
]
LINT_REQUIRED = ["{context}", "{question}", "[N]", "I read that as"]

TARGET_KINDS = ("chunk", "source", "shape", "candidate", "lesson", "question")
LEDGER_TYPES = ("confirm", "correct", "prior", "decay", "veto", "gate", "promote", "lesson", "usage")


@dataclass
class Citation:
    n: int
    chunk_id: str
    title: str
    link: str
    snippet: str
    cosine: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LedgerRow:
    target: str
    target_kind: str  # one of TARGET_KINDS
    type: str  # one of LEDGER_TYPES
    delta: float
    before: float
    after: float
    cause: str

    def to_dict(self) -> dict:
        return asdict(self)
