"""Console module split guard (feature 61): no oversized inline script, module graph resolves,
server serves the entrypoint. Static-file checks plus one ASGI request — no CLI/DB/network."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
JS_DIR = STATIC_DIR / "js"
ENTRYPOINT = "/static/js/main.js"

# Feature 61: an inline script block may exist (bootstrap, analytics) but must stay small
# enough that console work never collides on one file again.
MAX_INLINE_SCRIPT_LINES = 50


def _index_html() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _script_blocks(html: str) -> list[tuple[str, int]]:
    """Return (opening_tag, block_line_count) for every <script>...</script> block."""
    blocks = []
    for m in re.finditer(r"<script\b[^>]*>.*?</script>", html, re.DOTALL):
        opening = re.match(r"<script\b[^>]*>", m.group(0)).group(0)
        blocks.append((opening, m.group(0).count("\n") + 1))
    return blocks


def test_no_inline_script_block_over_50_lines() -> None:
    for opening, lines in _script_blocks(_index_html()):
        assert "src=" in opening or lines <= MAX_INLINE_SCRIPT_LINES, (
            f"inline script block ({lines} lines) exceeds {MAX_INLINE_SCRIPT_LINES}; "
            "console code belongs in app/static/js/ modules (feature 61)"
        )


def test_module_entrypoint_is_referenced() -> None:
    html = _index_html()
    module_tags = [o for o, _ in _script_blocks(html) if 'type="module"' in o]
    assert module_tags, "index.html must load the console as an ES module"
    assert any(ENTRYPOINT in tag for tag in module_tags)


def test_every_local_import_resolves() -> None:
    """Every './x.js' import specifier must name a file under app/static/js/.

    Regression guard for the split itself: a module that imports a path nobody
    wrote fails here instead of blank-screening the console.
    """
    files = list(JS_DIR.glob("*.js"))
    assert files, "app/static/js/ must contain the console modules"
    missing: list[str] = []
    for f in files:
        for spec in re.findall(r'from\s+"(\./[^"]+)"', f.read_text(encoding="utf-8")):
            if not (f.parent / spec).is_file():
                missing.append(f"{f.name} -> {spec}")
    assert not missing, f"unresolved module imports: {missing}"


@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_root_references_entrypoint_and_modules_are_served(client) -> None:
    async with client as c:
        page = await c.get("/")
        assert page.status_code == 200
        assert ENTRYPOINT in page.text

        module = await c.get(ENTRYPOINT)
        assert module.status_code == 200
        assert "javascript" in module.headers["content-type"].lower()
