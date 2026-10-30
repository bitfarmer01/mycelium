"""Silent-demo toggle (feature 60): the switch skips every TTS path so a demo
burns zero ElevenLabs credits, while captions keep showing the spoken text.

The flag logic is executed for real under node (a localStorage shim stands in
for the browser); the speak() guard and checkbox wiring are asserted statically,
in the same style as test_console_modules.py.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
JS_DIR = STATIC_DIR / "js"

_NODE_BEHAVIOR_SCRIPT = """
const store = new Map();
globalThis.window = {
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  },
};
const mod = await import(process.argv[2]);

const assert = (cond, msg) => { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } };

assert(mod.KEY === "mycelium.silent", "persistence key");
assert(mod.isSilent() === false, "starts unset");
mod.setSilent(true);
assert(mod.isSilent() === true, "setSilent(true) persists");
assert(store.get(mod.KEY) === "1", "localStorage carries the flag");
mod.setSilent(true); // idempotent
assert(mod.isSilent() === true, "idempotent set");
mod.setSilent(false);
assert(mod.isSilent() === false, "setSilent(false) clears");
assert(!store.has(mod.KEY), "cleared from storage");
console.log("SILENT_FLAG_OK");
"""


def test_silent_flag_persists_in_storage() -> None:
    """Real execution of silent.js: the toggle round-trips through localStorage."""
    silent_mjs = JS_DIR / "silent.js"
    workdir = Path(__file__).parent / "_silent_js_tmp"
    workdir.mkdir(exist_ok=True)
    shutil.copy(silent_mjs, workdir / "silent.mjs")
    script = workdir / "behavior.mjs"
    script.write_text(_NODE_BEHAVIOR_SCRIPT, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["node", str(script), str(workdir / "silent.mjs")],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    assert proc.returncode == 0, f"node behavior test failed:\n{proc.stdout}\n{proc.stderr}"
    assert "SILENT_FLAG_OK" in proc.stdout


def test_speak_guard_runs_before_any_audio_or_tts_path() -> None:
    """audio.js must consult isSilent() before constructing Audio (which fetches
    /tts) or falling back to speechSynthesis — a guard placed after the fetch
    would burn the credit it exists to protect."""
    audio = (JS_DIR / "audio.js").read_text(encoding="utf-8")
    guard = audio.find("if (isSilent())")
    first_audio = audio.find("new Audio(")
    # search after the guard: the browserSpeak(text) definition itself comes earlier
    browser_fallback = audio.find("browserSpeak(text)", guard)
    assert guard != -1, "speak() has no silent guard"
    assert guard < first_audio, "silent guard must precede Audio construction"
    assert guard < browser_fallback, "silent guard must precede the speechSynthesis fallback"


def test_captions_render_independent_of_audio() -> None:
    """'Captions still run': answer.js writes #spoken text before (or without)
    any speak() call, so silencing audio never blanks the transcript."""
    answer = (JS_DIR / "answer.js").read_text(encoding="utf-8")
    caption = answer.find('$("#spoken").textContent')
    speak_call = answer.find("speak(res.spoken")
    assert caption != -1, "answer.js renders no #spoken caption"
    assert speak_call == -1 or caption < speak_call, (
        "caption must be written before audio playback is attempted"
    )


def test_console_has_persisted_silent_checkbox() -> None:
    """index.html carries the toggle, labeled and keyboard-operable."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="silentMode"' in html, "console has no silent-demo toggle"
    assert "silentMode" in html and "for=" in html, "toggle lacks a clickable label"


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_silent_state_is_console_side_no_server_flag_needed(client) -> None:
    """The toggle works by never issuing the request — the server route stays a
    plain cached TTS endpoint. Sanity-check the route still exists and answers."""
    async with client as c:
        r = await c.get("/tts", params={"text": "silent mode keeps this endpoint"})
        assert r.status_code in (200, 502)  # 200 with key+network, 502 without — never 4xx from a silent flag
