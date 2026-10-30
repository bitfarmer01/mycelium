"""Reading-chip redesign (feature 63): taken chip visually dominant, flip_condition
tooltip on hover/focus, keyboard operable. Static checks on the console modules;
interaction evidence is captured via agent-browser dogfooding against the head SHA."""

from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
JS_DIR = STATIC_DIR / "js"


def _js(name: str) -> str:
    return (JS_DIR / name).read_text(encoding="utf-8")


def _html() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def test_taken_chip_stays_keyboard_focusable() -> None:
    """`disabled` buttons leave the tab order — the tooltip must not depend on a pointer."""
    src = _js("corrections.js")
    assert "chip.disabled" not in src, (
        "taken chip must not be disabled; it stays keyboard-focusable so the "
        "flip_condition tooltip is operable (feature 63)"
    )
    assert 'aria-disabled' in src


def test_flip_condition_tooltip_is_wired_for_at() -> None:
    src = _js("corrections.js")
    assert 'role", "tooltip' in src, "tooltip element must carry role=tooltip"
    assert "aria-describedby" in src, "taken chip must describe the tooltip for AT"
    assert "flip_condition" in src, "tooltip content must come from the answer payload"


def test_tooltip_revealed_on_hover_and_keyboard_focus() -> None:
    css = _html()
    for rule in (".chip:hover .tip", ".chip:focus-visible .tip"):
        assert rule in css, f"tooltip must reveal on {rule}"
    # hidden by default, revealed only by hover/focus
    assert ".chip .tip{" in css and "display:none" in css


def test_taken_chip_is_visually_dominant() -> None:
    css = _html()
    taken = re.search(r"\.chip\.taken\{[^}]*\}", css)
    assert taken, "taken chip must have dedicated dominant styling"
    assert "var(--glow-g)" in taken.group(0) and "2px solid var(--green-b)" in taken.group(0)
