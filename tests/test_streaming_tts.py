"""Streaming TTS (feature 51): audio starts after sentence one is synthesized.

The mock replaces the ElevenLabs HTTP boundary (`_tts_request`) and delays each
synthesis, so the timing assertion measures real wall-clock behavior of the
generator: the first chunk must be yieldable while later sentences are still
synthesizing. Time-to-first-audio < full-answer TTS time, as the spec requires.
"""

from __future__ import annotations

import time

import pytest

from core import voice

TEST_VOICE = "test-voice-id"
SENTENCES = [
    "Mycelium answers from your own wiki.",
    "Each claim carries its citation.",
    "Corrections move the priors.",
]


def _slow_counter(monkeypatch, delay: float = 0.05, payload: bytes = b"mp3") -> list[float]:
    """Record completion time of each synthesis call; slow them so ordering is measurable."""
    call_times: list[float] = []

    def fake_request(text: str, vid: str, quality: str) -> bytes:
        time.sleep(delay)
        call_times.append(time.monotonic())
        return payload

    monkeypatch.setattr(voice, "_tts_request", fake_request)
    monkeypatch.setattr(voice, "_voice_id", TEST_VOICE)  # skips the voices HTTP lookup
    return call_times


def test_first_chunk_before_last_sentence_is_synthesized(monkeypatch) -> None:
    call_times = _slow_counter(monkeypatch)
    voice.tts_cache_clear()

    first_chunk_time: float | None = None
    chunks = []
    for chunk in voice.tts_stream(" ".join(SENTENCES)):
        chunks.append(chunk)
        if first_chunk_time is None:
            first_chunk_time = time.monotonic()

    assert first_chunk_time is not None
    assert len(chunks) == len(SENTENCES), "one chunk per sentence"
    assert len(call_times) == len(SENTENCES)
    assert first_chunk_time < call_times[-1], (
        "time-to-first-audio must precede the final sentence's synthesis completion"
    )


def test_stream_makes_one_api_call_per_sentence(monkeypatch) -> None:
    call_times = _slow_counter(monkeypatch, delay=0.0)
    voice.tts_cache_clear()

    chunks = list(voice.tts_stream(" ".join(SENTENCES)))

    assert len(call_times) == len(SENTENCES), "sentences synthesize individually, not as one blob"
    assert all(chunks), "every chunk carries audio bytes"


def test_stream_reuses_the_tts_cache_across_streams(monkeypatch) -> None:
    call_times = _slow_counter(monkeypatch, delay=0.0)
    voice.tts_cache_clear()

    text = " ".join(SENTENCES)
    list(voice.tts_stream(text))
    list(voice.tts_stream(text))

    assert len(call_times) == len(SENTENCES), (
        "second identical stream must come entirely from the cache"
    )


def test_stream_and_full_tts_share_cache_entries(monkeypatch) -> None:
    call_times = _slow_counter(monkeypatch, delay=0.0)
    voice.tts_cache_clear()

    sentence = SENTENCES[0]
    streamed = next(iter(voice.tts_stream(sentence)))
    full = voice.tts(sentence)

    assert streamed == full
    assert len(call_times) == 1, "same spoken string costs one API call regardless of path"


def test_empty_text_raises_before_any_api_call(monkeypatch) -> None:
    call_times = _slow_counter(monkeypatch)
    voice.tts_cache_clear()

    with pytest.raises(voice.VoiceError):
        list(voice.tts_stream("   "))
    assert call_times == []


def test_unsplittable_text_still_streams_one_chunk(monkeypatch) -> None:
    call_times = _slow_counter(monkeypatch, delay=0.0)
    voice.tts_cache_clear()

    chunks = list(voice.tts_stream("just a fragment"))

    assert chunks == [b"mp3"]
    assert len(call_times) == 1


def test_route_registers_streaming_endpoint() -> None:
    """The route manifest pins legacy routes; this pins the new streaming route."""
    from app.server import app

    assert app.url_path_for("tts_stream") == "/tts/stream"
