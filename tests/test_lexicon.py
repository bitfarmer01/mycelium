"""Pronunciation lexicon (feature 59): product names TTS mangles get phonetic
replacements applied before synthesis.

The mock replaces the ElevenLabs HTTP boundary (`_tts_request`), so every
assertion below sees the exact text that would reach the API.
"""

from __future__ import annotations

from core import voice

TEST_VOICE = "test-voice-id"


def _capture(monkeypatch, payload: bytes = b"mp3") -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []

    def fake_request(text: str, vid: str, quality: str) -> bytes:
        calls.append((text, vid, quality))
        return payload

    monkeypatch.setattr(voice, "_tts_request", fake_request)
    monkeypatch.setattr(voice, "_voice_id", TEST_VOICE)
    return calls


def test_lexicon_maps_required_names():
    assert voice.apply_lexicon("Mycelium") != "Mycelium"
    assert voice.apply_lexicon("SurrealDB") != "SurrealDB"
    assert "my-SEE-lee-um" == voice.apply_lexicon("Mycelium")
    assert "SURREAL dee bee" == voice.apply_lexicon("SurrealDB")


def test_lexicon_applies_inside_sentences():
    out = voice.apply_lexicon("Mycelium stores its facts in SurrealDB.")
    assert "Mycelium" not in out
    assert "SurrealDB" not in out
    assert out == "my-SEE-lee-um stores its facts in SURREAL dee bee."


def test_lexicon_is_case_insensitive_on_the_source():
    assert voice.apply_lexicon("mycelium") == "my-SEE-lee-um"
    assert voice.apply_lexicon("surrealdb") == "SURREAL dee bee"


def test_lexicon_does_not_touch_other_words():
    text = "The knowledge base of a company; we love databases."
    assert voice.apply_lexicon(text) == text


def test_lexicon_matches_whole_words_not_substrings():
    text = "surrealdbx is not SurrealDB."
    out = voice.apply_lexicon(text)
    assert out.startswith("surrealdbx")  # substring untouched
    assert out.endswith("SURREAL dee bee.")


def test_lexicon_is_idempotent():
    once = voice.apply_lexicon("Mycelium answers over SurrealDB")
    assert voice.apply_lexicon(once) == once


def test_tts_applies_lexicon_before_the_api(monkeypatch):
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()

    voice.tts("Ask Mycelium about SurrealDB.")

    assert calls[0][0] == "Ask my-SEE-lee-um about SURREAL dee bee."


def test_cache_keys_use_the_lexiconized_text(monkeypatch):
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()

    # "SURREAL dee bee" and "SurrealDB" both lexiconize to the same spoken
    # string — one API call, one cache entry.
    first = voice.tts("SurrealDB")
    second = voice.tts("SURREAL dee bee")

    assert first == second
    assert len(calls) == 1
    assert calls[0][0] == "SURREAL dee bee"


def test_stream_applies_lexicon_per_sentence(monkeypatch):
    calls = _capture(monkeypatch)
    voice.tts_cache_clear()

    chunks = list(voice.tts_stream("Mycelium learns. SurrealDB remembers."))

    assert len(chunks) == 2
    assert {text for text, _, _ in calls} == {"my-SEE-lee-um learns.", "SURREAL dee bee remembers."}
