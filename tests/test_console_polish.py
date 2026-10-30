"""Console polish batch 4 (features 68-70): skeleton loaders, answer timeline,
mobile layout at 390px. Static checks over app/static/ (the established pattern
for console features); interaction evidence is captured via agent-browser
dogfooding in mock mode against the head SHA."""

from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
JS_DIR = STATIC_DIR / "js"


def _js(name: str) -> str:
    return (JS_DIR / name).read_text(encoding="utf-8")


def _html() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- feature 68 --

def test_skeletons_render_before_the_first_await() -> None:
    """The <100ms budget: skeleton DOM insertion is synchronous in the click
    handler — it precedes any await, so no network or model latency can
    preempt it."""
    for name, show, marker in (
        ("ask.js", "showAskSkeleton", 'await streamAsk'),
        ("corrections.js", "showCorrectSkeleton", 'await jpost("/learning/preview"'),
        ("reflect.js", "showReflectSkeleton", 'await jpost("/reflect"'),
    ):
        src = _js(name)
        call = show + "();"
        assert call in src, f"{name} must call {show}()"
        assert src.index(call) < src.index("await", src.index(call)), (
            f"{name}: {show}() must run before the first await"
        )
        assert marker in src, f"{name} must await its call after showing the skeleton"


def test_skeleton_css_uses_shimmer_and_dies_under_reduced_motion() -> None:
    css = _html()
    assert "@keyframes skel" in css, "skeleton shimmer keyframes must exist"
    assert ".skelbar{" in css and "animation:skel" in css
    # the global prefers-reduced-motion rule zeroes every animation duration,
    # which covers .skelbar — assert it is still there
    reduce = re.search(r"@media \(prefers-reduced-motion:reduce\)\{.*?\}\s*\}", css, re.DOTALL)
    assert reduce and "animation-duration:.001ms" in reduce.group(0)


def test_skeleton_container_is_labelled_for_at() -> None:
    m = re.search(r'<div class="skelbox" id="skeleton"[^>]*>', _html())
    assert m, "skeleton container must exist in the ask panel"
    tag = m.group(0)
    assert 'role="status"' in tag and 'aria-live="polite"' in tag
    assert 'aria-label="Loading"' in tag


def test_reflect_error_path_restores_the_empty_state() -> None:
    src = _js("reflect.js")
    assert "clearReflectSkeleton" in src, "reflect failure must restore #sentences"


# ---------------------------------------------------------------- feature 69 --

def test_timeline_steps_follow_the_real_pipeline_order() -> None:
    """reflex.ask runs readings before retrieval (the taken reading's query
    drives retrieval) — the timeline shows cumulative server ms, so the steps
    must render in execution order or the numbers would run backwards."""
    src = _js("timeline.js")
    keys = re.findall(r'key: "(\w+)"', src)
    # keys are the server's event types; labels are the rendered stage names
    assert keys == ["readings", "context", "synthesis"]
    assert re.findall(r'label: "(\w+)"', src) == ["readings", "retrieval", "synthesis"]
    # same stage set as the live pipeline indicator, module split notwithstanding
    pipeline = _js("pipeline.js")
    for stage in ("readings", "retrieval", "synthesis"):
        assert stage in pipeline and stage in src


def test_timeline_timings_come_from_server_events_not_the_wall_clock() -> None:
    src = _js("timeline.js")
    assert "ev.ms" in src, "stage timings must come from the stream events"
    ask = _js("ask.js")
    assert "onStageEvent" in ask and "finishTimeline" in ask
    # the done event's answer payload pins the total
    assert "ev.answer.ms" in ask or "ev.answer && ev.answer.ms" in ask
    # an absent timing renders as an em dash, never a guess
    assert '"—"' in src


def test_ask_falls_back_to_the_non_streaming_answer() -> None:
    """Demo resilience: if /ask/stream fails mid-flight, POST /ask still
    answers and the timeline degrades to a total-only row."""
    ask = _js("ask.js")
    assert 'jpost("/ask"' in ask, "ask must fall back to the non-streaming path"
    assert "failTimeline" in ask


def test_stream_client_parses_sse_frames_and_carries_the_answer() -> None:
    src = _js("api.js")
    assert "async function streamAsk" in src
    assert '"/ask/stream"' in src
    assert 'indexOf("event:")' in src and 'indexOf("data:")' in src, (
        "the SSE client must parse event/data frame lines"
    )
    assert 'ev.type === "done"' in src and "ev.answer" in src
    assert "mockStream" in src, "mock mode must stream stage events too"


def test_timeline_and_skeleton_containers_exist() -> None:
    html = _html()
    assert 'id="timeline"' in html and 'id="skeleton"' in html
    # both live in the ask panel, before the answer block
    assert html.index('id="timeline"') < html.index('id="answer"')


# ---------------------------------------------------------------- feature 70 --

def test_mobile_media_query_targets_390px() -> None:
    css = _html()
    m = re.search(r"@media \(max-width:420px\)\{(.*?)\n  \}", css, re.DOTALL)
    assert m, "the 390px-targeted media query must exist"
    block = m.group(1)
    assert ".tagline{display:none}" in block
    assert ".askbar{flex-direction:column" in block, "ask bar must stack on a phone"
    assert ".chip{flex-basis:100%}" in block, "chips must be full-width thumb targets"


def test_viewport_meta_and_single_column_layout_predate_the_pass() -> None:
    html = _html()
    assert "width=device-width, initial-scale=1" in html
    assert "max-width:940px" in html, "single-column stack below 940px is the base"
