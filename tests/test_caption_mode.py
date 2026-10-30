"""Caption-always mode (feature 56): the transcript renders beside every spoken
answer, and the toggle persists in localStorage. The flag logic is executed for
real under node (a localStorage shim stands in for the browser) — the same style
as test_silent_mode.py; the visibility wiring is asserted statically."""

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

assert(mod.KEY === "mycelium.captions", "persistence key");
assert(mod.isCaptions() === true, "captions default ON when the key is unset");
mod.setCaptions(false);
assert(mod.isCaptions() === false, "setCaptions(false) hides captions");
assert(store.get(mod.KEY) === "0", "localStorage carries the OFF flag");
mod.setCaptions(false); // idempotent
assert(mod.isCaptions() === false, "idempotent set");
mod.setCaptions(true);
assert(mod.isCaptions() === true, "setCaptions(true) restores captions");
assert(!store.has(mod.KEY), "ON is stored as key absence (default)");
console.log("CAPTIONS_FLAG_OK");
"""


def test_captions_flag_persists_in_storage() -> None:
    """Real execution of captions.js: the toggle round-trips through localStorage."""
    captions_mjs = JS_DIR / "captions.js"
    workdir = Path(__file__).parent / "_captions_js_tmp"
    workdir.mkdir(exist_ok=True)
    shutil.copy(captions_mjs, workdir / "captions.mjs")
    script = workdir / "behavior.mjs"
    script.write_text(_NODE_BEHAVIOR_SCRIPT, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["node", str(script), str(workdir / "captions.mjs")],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    assert proc.returncode == 0, f"node behavior test failed:\n{proc.stdout}\n{proc.stderr}"
    assert "CAPTIONS_FLAG_OK" in proc.stdout


def test_console_has_persisted_captions_checkbox() -> None:
    """index.html carries the toggle, labeled and keyboard-operable."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="captionsMode"' in html, "console has no caption-always toggle"
    assert "for=\"captionsMode\"" in html, "toggle lacks a clickable label"


def test_caption_block_visibility_follows_the_toggle() -> None:
    """answer.js must hide/show the spoken block from the persisted preference,
    and re-apply it live when the toggle changes."""
    answer = (JS_DIR / "answer.js").read_text(encoding="utf-8")
    assert 'id="spokenBlock"' in (STATIC_DIR / "index.html").read_text(encoding="utf-8"), (
        "the spoken block needs an id for the visibility gate"
    )
    assert '$("#spokenBlock")' in answer, "answer.js never applies the captions preference"
    assert "!isCaptions()" in answer, "hidden state must come from the persisted captions flag"
    assert "CAPTIONS_CHANGE_EVENT" in answer, "toggling mid-session must re-apply without reload"


def test_caption_writes_before_speak_unchanged() -> None:
    """Regression guard: #spoken text is still written before playback is attempted,
    so switching captions off never stops the audio itself."""
    answer = (JS_DIR / "answer.js").read_text(encoding="utf-8")
    caption = answer.find('$("#spoken").textContent')
    speak_call = answer.find("speak(res.spoken")
    assert caption != -1 and (speak_call == -1 or caption < speak_call)


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_captions_are_console_side_no_server_flag_needed(client) -> None:
    """The toggle gates rendering only — the server route stays a plain endpoint."""
    async with client as c:
        r = await c.get("/tts", params={"text": "captions toggle does not gate this endpoint"})
        assert r.status_code in (200, 502)  # 200 with key+network, 502 without — never 4xx from a captions flag
