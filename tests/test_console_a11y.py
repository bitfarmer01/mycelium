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
    assert "tabIndex = 0" in answer_js, "citations must join the tab order"
    assert 'role", "button"' in answer_js or 'role", \n    "button"' in answer_js
    assert 'ev.key === "Enter"' in answer_js, "citations must fire on Enter"


def test_drop_zone_has_keyboard_activation() -> None:
    upload_js = (JS_DIR / "upload.js").read_text(encoding="utf-8")
    assert 'e.key === "Enter" || e.key === " "' in upload_js, (
        "drop zone must activate on Enter and Space (feature 65)"
    )


# ---------------------------------------------------------------- feature 66 --


def _luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of an #rrggbb color."""
    r, g, b = (int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(fg: str, bg: str) -> float:
    l1, l2 = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _root_tokens(css: str) -> dict[str, str]:
    m = re.search(r":root\{(.*?)\}", css, re.DOTALL)
    assert m, ":root token block missing"
    return dict(re.findall(r"(--[\w-]+):([#\w().,/ -]+?)(?=;|$)", m.group(1)))


def test_dark_theme_text_tokens_meet_aa() -> None:
    """Feature 66: every text token passes AA (4.5:1) on the lightest surface it
    can render on. --dim guards the worst case --s4; other tokens their own pair."""
    tokens = _root_tokens(_css())
    pairs = [
        ("--ink", "--s4"),
        ("--muted", "--s4"),
        ("--dim", "--s4"),  # hint/label text may sit on any surface
        ("--green-t", "--green-l"),
        ("--red-t", "--red-l"),
        ("--amber-t", "--amber-l"),
        ("--grey-t", "--grey-l"),
        ("--blue-t", "--blue-l"),
    ]
    failures = [
        f"{fg} on {bg}: {tokens[fg]} vs {tokens[bg]} = {ratio:.2f}"
        for fg, bg in pairs
        if (ratio := _contrast(tokens[fg], tokens[bg])) < 4.5
    ]
    assert not failures, "AA contrast violations in dark theme: " + "; ".join(failures)


# ---------------------------------------------------------------- feature 67 --


def _theme_tokens(theme: str) -> dict[str, str]:
    m = re.search(rf'html\[data-theme="{theme}"\]\{{(.*?)\}}', _css(), re.DOTALL)
    assert m, f'html[data-theme="{theme}"] token block missing (feature 67)'
    return dict(re.findall(r"(--[\w-]+):([#\w().,/ -]+?)(?=;|$)", m.group(1)))


def _assert_aa(
    tokens: dict[str, str], pairs: list[tuple[str, str]], theme: str
) -> None:
    failures = [
        f"{fg} on {bg}: {tokens[fg]} vs {tokens[bg]} = {ratio:.2f}"
        for fg, bg in pairs
        if (ratio := _contrast(tokens[fg], tokens[bg])) < 4.5
    ]
    assert not failures, f"AA contrast violations in {theme} theme: " + "; ".join(
        failures
    )


def test_light_theme_tokens_meet_aa() -> None:
    tokens = _theme_tokens("light")
    _assert_aa(
        tokens,
        [
            ("--ink", "--s4"),
            ("--muted", "--s4"),
            ("--dim", "--s4"),
            ("--dim", "--bg"),
            ("--green-t", "--green-l"),
            ("--red-t", "--red-l"),
            ("--amber-t", "--amber-l"),
            ("--grey-t", "--grey-l"),
            ("--blue-t", "--blue-l"),
            ("--green", "--s1"),
            ("--red", "--s1"),
            ("--amber", "--s1"),
            ("--blue", "--s1"),
        ],
        "light",
    )


def test_dim_theme_tokens_meet_aa() -> None:
    tokens = _theme_tokens("dim")
    _assert_aa(
        tokens,
        [("--ink", "--s4"), ("--muted", "--s4"), ("--dim", "--s4")],
        "dim",
    )


def test_theme_module_cycles_without_reload() -> None:
    js = (JS_DIR / "theme.js").read_text(encoding="utf-8")
    assert "dataset.theme" in js, "theme must apply via data-theme on <html>"
    assert "localStorage" in js, "theme preference must persist"
    assert "cycleTheme" in js and "THEMES.indexOf" in js
    assert "colorScheme" in js, "color-scheme hint must follow the theme"


def test_main_wires_theme_and_header_has_toggle() -> None:
    assert 'import "./theme.js"' in (JS_DIR / "main.js").read_text(encoding="utf-8")
    html = _index_html()
    assert 'id="themeBtn"' in html, "header must carry the theme toggle"
    assert 'data-theme="light"' in html and 'data-theme="dim"' in html


def test_reduced_motion_disables_glow_and_animation() -> None:
    css = _css()
    m = re.search(
        r"@media \(prefers-reduced-motion:reduce\)\{(.*?)\}\s*@media", css, re.DOTALL
    )
    assert m, "prefers-reduced-motion block missing"
    block = m.group(1)
    assert "animation:none" in block, "reduced motion must stop animations"
    assert "box-shadow:none" in block, "reduced motion must disable glow shadows"
