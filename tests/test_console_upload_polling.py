"""Console follows the PR #26 background-ingest contract: the 202 body is an
acknowledgement (job_id, status, files, rejected) — not an ingest result. The console
must poll GET /upload/status/{job_id} and render only the finished result.

Static checks in the test_provenance_cards style; the live 202 → poll → done flow is
exercised in the browser against the booted stack (PR dogfood evidence).
"""

from __future__ import annotations

import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _js(name: str) -> str:
    return (JS_DIR / name).read_text(encoding="utf-8")


def test_upload_does_not_render_the_202_body_as_a_result() -> None:
    """Regression guard for the integration addendum: renderUploadResult used to be fed
    the 202 acknowledgement directly, so chunks/ms never appeared (they only exist once
    the background job finishes)."""
    src = _js("upload.js")
    assert not re.search(r"renderUploadResult\s*\(\s*await\s+jpostForm", src), (
        "upload.js must not render the POST /upload acknowledgement as an ingest result"
    )


def test_upload_polls_status_endpoint_until_done() -> None:
    src = _js("upload.js")
    assert re.search(
        r"renderUploadResult\s*\(\s*await\s+pollUpload\s*\(\s*ack\s*\)", src
    ), "upload.js must feed renderUploadResult from pollUpload(ack), the status poll"
    assert 'jpostForm("/upload"' in src, "the upload POST itself is unchanged"
    assert "renderClientRejection(ack.rejected)" in src, (
        "server-side pre-check rejections ride the 202 body and must render immediately"
    )


def test_poll_upload_walks_the_status_contract() -> None:
    """api.js pollUpload: done → the ingest result; failed → an error carrying the job's
    reason; 404 → the job expired. A payload that already carries the result (mock mode,
    legacy sync server) passes through without polling."""
    src = _js("api.js")
    poll = src[
        src.find("function uploadNeedsPolling") : src.find(
            "/* ---- mock implementations ---- */"
        )
    ]
    assert "/upload/status/" in poll, "pollUpload must hit GET /upload/status/{job_id}"
    assert 'job.status === "done"' in poll and "job.result" in poll, (
        "done carries the ingest result"
    )
    assert 'job.status === "failed"' in poll and "job.error" in poll, (
        "failed carries the job's error"
    )
    assert "404" in poll, "a 404 means the job expired — stop polling, surface it"
    assert re.search(r"res\.job_id\s*!=\s*null\s*&&\s*res\.result\s*==\s*null", poll), (
        "an ack without a job id (mock mode / legacy sync server) must pass straight through"
    )


def test_poll_upload_is_exported_from_the_api_layer() -> None:
    assert re.search(r"export\s*\{[^}]*\bpollUpload\b", _js("api.js")), (
        "pollUpload lives in the api layer (one place for every request) and is exported"
    )
