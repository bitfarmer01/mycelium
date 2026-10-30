"""Console accessibility guards (features 65, 66, 67).

Static checks over app/static/ plus a pure-math WCAG contrast scan of the theme
tokens. No CLI/DB/network — the browser-level axe scan and keyboard Tab-walk are
run against a live console and attached as PR evidence (features 65, 66).
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
JS_DIR = STATIC_DIR / "js"


def _index_html() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _css() -> str:
    html = _index_html()
    m = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    assert m, "index.html must carry a <style> block"
    return m.group(1)


# ---------------------------------------------------------------- feature 65 --


def test_universal_focus_visible_rule_exists() -> None:
    """Every interactive element gets a visible ring even without its own rule."""
    assert re.search(r":where\([^)]*\):focus-visible", _css()), (
        "universal :focus-visible fallback missing (feature 65)"
    )


def test_index_interactive_elements_are_focusable() -> None:
    """Native interactive elements need nothing; ARIA-widget divs need tabindex+role."""
    html = _index_html()
    body = re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)
    # role="button" divs must be keyboard-reachable
    for m in re.finditer(r'<(\w+)[^>]*role="button"[^>]*>', body):
        tag_attrs = m.group(0)
        assert 'tabindex="0"' in tag_attrs, (
            f'role="button" element is not keyboard-reachable: {tag_attrs[:80]}'
        )
    # nothing may opt out of the tab order while remaining interactive
    assert not re.search(r'tabindex="-1"', body), (
        "tabindex=-1 found: interactive elements must stay Tab-reachable (feature 65)"
    )


def test_citation_sups_are_keyboard_operable() -> None:
    """sup.cite nodes are created dynamically — they must be focusable buttons."""
    answer_js = (JS_DIR / "answer.js").read_text(encoding="utf-8")
    assert 'tabIndex = 0' in answer_js, "citations must join the tab order"
    assert 'role", "button"' in answer_js or 'role", \n    "button"' in answer_js
    assert 'ev.key === "Enter"' in answer_js, "citations must fire on Enter"


def test_drop_zone_has_keyboard_activation() -> None:
    upload_js = (JS_DIR / "upload.js").read_text(encoding="utf-8")
    assert 'e.key === "Enter" || e.key === " "' in upload_js, (
        "drop zone must activate on Enter and Space (feature 65)"
    )
