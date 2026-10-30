"""ElevenLabs adapters: tts(text)->mp3 bytes, stt(audio, mime)->text. Optional feature; callers fall back on failure."""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator

import httpx

BASE = "https://api.elevenlabs.io"
# Feature 55: named quality selector. "flash" is the default — cheapest and
# fastest; "v3" is the higher-fidelity tier. A raw model id also passes through.
QUALITY_MODELS = {"flash": "eleven_flash_v2_5", "v3": "eleven_v3"}
TTS_MODEL = QUALITY_MODELS["flash"]
STT_MODEL = "scribe_v1"
TIMEOUT = 30.0

_voice_id: str | None = None

# Feature 59: pronunciation lexicon. Product names TTS mangles get phonetic
# replacements applied before any synthesis call (and before the cache key,
# so cached audio is always lexicon-correct).
LEXICON = {
    "Mycelium": "my-SEE-lee-um",
    "SurrealDB": "SURREAL dee bee",
}
_LEXICON_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in LEXICON) + r")\b", re.IGNORECASE
)
_LEXICON_LOWER = {k.lower(): v for k, v in LEXICON.items()}


def apply_lexicon(text: str) -> str:
    """Feature 59: replace lexiconed product names with phonetic spellings.
    Idempotent — replacements never re-match the lexicon."""
    return _LEXICON_RE.sub(lambda m: _LEXICON_LOWER[m.group(0).lower()], text)


# Feature 52: TTS cache keyed by (quality, voice, text). One spoken string costs
# one API call for the life of the process — the demo budget is ~370 spoken answers.
_tts_cache: dict[str, bytes] = {}


def tts_cache_clear() -> None:
    """Test/diagnostic hook: drop every cached synthesis."""
    _tts_cache.clear()


def _tts_cache_key(text: str, voice: str, quality: str) -> str:
    raw = f"tts\x00{quality}\x00{voice}\x00{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class VoiceError(RuntimeError):
    """Raised on any voice failure. Never carries the API key."""


def _headers() -> dict:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY not set")
    return {"xi-api-key": key}


def _voice(client: httpx.Client) -> str:
    global _voice_id
    if _voice_id:
        return _voice_id
    override = os.environ.get("ELEVENLABS_VOICE_ID")
    if override:
        _voice_id = override
        return _voice_id
    r = client.get(f"{BASE}/v1/voices", headers=_headers())
    if r.status_code != 200:
        raise VoiceError(f"voices lookup failed: HTTP {r.status_code}")
    voices = r.json().get("voices") or []
    if not voices:
        raise VoiceError("no voices available")
    _voice_id = voices[0]["voice_id"]
    return _voice_id


def _tts_request(text: str, voice: str, quality: str) -> bytes:
    """Single ElevenLabs synthesis call. Extracted so tests can mock the API boundary."""
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(
            f"{BASE}/v1/text-to-speech/{voice}",
            params={"output_format": "mp3_44100_128"},
            headers=_headers(),
            json={"text": text, "model_id": quality},
        )
    if r.status_code != 200:
        raise VoiceError(f"tts failed: HTTP {r.status_code}")
    return r.content


def resolve_quality(quality: str | None) -> str:
    """Feature 55: map a selector name (flash | v3, case-insensitive) to its
    ElevenLabs model id; unknown values pass through as raw model ids."""
    if not quality:
        return TTS_MODEL
    named = QUALITY_MODELS.get(quality.strip().lower())
    return named or quality.strip()


def tts(text: str, quality: str | None = None) -> bytes:
    """Text to mp3 bytes. Cached by (quality, voice, text): a repeated spoken
    string never re-hits the API (feature 52). `quality` accepts a selector
    name (flash | v3) or a raw model id (feature 55). The pronunciation
    lexicon is applied before synthesis and before the cache key (feature 59)."""
    spoken = apply_lexicon(text)
    if not spoken or not spoken.strip():
        raise VoiceError("empty text")
    q = resolve_quality(quality)
    with httpx.Client(timeout=TIMEOUT) as client:
        vid = _voice(client)
    key = _tts_cache_key(spoken, vid, q)
    cached = _tts_cache.get(key)
    if cached is not None:
        return cached
    audio = _tts_request(spoken, vid, q)
    _tts_cache[key] = audio
    return audio


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Sentence split for feature 51: synthesize one sentence at a time."""
    return [s for s in _SENTENCE_RE.split(text.strip()) if s.strip()]


def tts_stream(text: str, quality: str | None = None) -> Iterator[bytes]:
    """Feature 51: synthesize sentence-by-sentence so playback can start on the
    first sentence while later ones are still synthesizing. Lazy generator —
    each sentence only hits the API when its chunk is pulled. Shares the tts()
    cache, so streaming and full-answer synthesis never double-spend."""
    if not text or not text.strip():
        raise VoiceError("empty text")
    for sentence in _split_sentences(apply_lexicon(text)):
        yield tts(sentence, quality=quality)


def stt(audio: bytes, mime: str) -> str:
    """Audio bytes to transcript text."""
    if not audio:
        raise VoiceError("empty audio")
    ext = (mime or "audio/webm").split("/")[-1].split(";")[0] or "webm"
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(
            f"{BASE}/v1/speech-to-text",
            headers=_headers(),
            data={"model_id": STT_MODEL},
            files={"file": (f"audio.{ext}", audio, mime or "audio/webm")},
        )
    if r.status_code != 200:
        raise VoiceError(f"stt failed: HTTP {r.status_code}")
    return str(r.json().get("text", "")).strip()
