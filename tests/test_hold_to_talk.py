"""Hold-to-talk with transcript preview (feature 54): hold records, release
shows an EDITABLE transcript before anything is sent — the speaker fixes a
mis-heard word, then Send or Discard.

Static checks cover the wiring (they run everywhere, including CI containers
without a browser). The browser flow boots the real console in mock mode and
drives hold → release → edit → send via agent-browser; it runs wherever the
agent-browser CLI exists (dogfooding machines) and skips with a named reason
elsewhere. Interaction evidence is captured against the tested head SHA.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
JS_DIR = STATIC_DIR / "js"


def _html() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _js(name: str) -> str:
    return (JS_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- static wiring --


def test_console_has_transcript_preview_markup() -> None:
    html = _html()
    for ident in ("transcriptBox", "transcriptEdit", "transcriptSend", "transcriptDiscard"):
        assert f'id="{ident}"' in html, f"console markup missing #{ident}"
    # the preview is a textarea (editable), never a read-only rendering
    m = re.search(r"<textarea[^>]*id=\"transcriptEdit\"[^>]*>", html)
    assert m, "transcript preview must be a <textarea>"
    assert "readonly" not in m.group(0) and "disabled" not in m.group(0), (
        "the transcript preview must be editable before send (feature 54)"
    )
    assert html.count('id="transcriptBox"') == 1 and 'id="transcriptBox" hidden' in html, (
        "preview ships hidden; it appears only after release"
    )


def test_release_shows_preview_before_send() -> None:
    """Both transcript paths (real STT and mock) land in the preview, not
    straight into the ask input."""
    talk = _js("talk.js")
    assert "function showPreview" in talk, "talk.js has no transcript preview path"
    assert talk.count("showPreview(") >= 3, "release must route the transcript through the preview"
    assert "transcriptBox.hidden = false" in talk, "release must reveal the preview"


def test_send_submits_the_edited_transcript_and_discard_drops_it() -> None:
    talk = _js("talk.js")
    assert 'import { ask } from "./ask.js"' in talk, "Send must reuse the ask flow"
    send_idx = talk.find('transcriptSend.addEventListener')
    assert send_idx != -1, "Send is never wired"
    after_send = talk[send_idx:]
    assert "transcriptEdit.value" in after_send[:400], "Send must read the EDITED text"
    assert "ask()" in after_send[:400], "Send must submit through the ask flow"
    assert 'transcriptDiscard.addEventListener' in talk, "Discard is never wired"


# ------------------------------------------------------------------ browser flow --

_HAS_AGENT_BROWSER = shutil.which("agent-browser") is not None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_url():
    """Boot the real app (mock console mode; SurrealDB stays down — the console
    degrades by design) and yield its base URL."""
    port = _free_port()
    proc = subprocess.Popen(
        ["uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:  # connection refused until uvicorn binds
                time.sleep(0.3)
        else:
            pytest.fail("server did not become healthy in 30 s")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.skipif(not _HAS_AGENT_BROWSER, reason="agent-browser CLI not installed on this machine; static wiring checks above still ran")
def test_hold_release_edit_send_flow(live_url) -> None:
    """The acceptance dogfood: hold records → release shows an editable
    transcript → edit → Send asks with the edited text."""
    import json as _json

    def ab(*args: str) -> str:
        out = subprocess.run(["agent-browser", *args], check=False, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, f"agent-browser {args[0]} failed: {out.stderr}"
        return out.stdout

    ab("open", f"{live_url}/?mock=1")
    ab("set", "viewport", "1440", "900", "2")
    ab("wait", "#talkBtn")

    # hold: pointerdown starts recording, pointerup releases
    ab("eval", "document.getElementById('talkBtn').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
    assert "recording" in ab("get", "text", "#status").lower(), "hold did not start recording"
    ab("eval", "document.getElementById('talkBtn').dispatchEvent(new PointerEvent('pointerup', {bubbles: true}))")

    # release: the preview is visible and editable
    ab("wait", "#transcriptBox:not([hidden])")
    hidden_attr = ab("eval", "JSON.stringify({hidden: document.getElementById('transcriptBox').hidden, value: document.getElementById('transcriptEdit').value, editable: !document.getElementById('transcriptEdit').readOnly && !document.getElementById('transcriptEdit').disabled})")
    state = _json.loads(_json.loads(hidden_attr))
    assert state["hidden"] is False, "preview did not appear on release"
    assert state["editable"] is True, "preview is not editable"
    assert state["value"].strip(), "preview arrived empty"

    # edit the transcript, then Send — the edited text is what gets asked
    edited = "how do we run a discovery call — with the staged agenda?"
    ab("fill", "#transcriptEdit", edited)
    ab("click", "#transcriptSend")
    ab("wait", "#pipeline:not([hidden])")
    assert ab("eval", "document.getElementById('pipeline').hidden") == "false", "Send did not start the ask"
    ab("wait", "#answer:not([hidden])")  # mock ask resolves in ~1 s
    closed = ab("eval", "document.getElementById('transcriptBox').hidden")
    assert closed == "true", "preview stayed open after Send"
    ab("close", "--all")  # leave no daemon holding the fixture port
