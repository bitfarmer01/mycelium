"""llm router: the question-answering endpoints (readings + synthesis).

``/ask`` is the original non-streaming surface; ``/ask/stream`` (feature 3)
emits the same pipeline as server-sent events so the console shows progress
while synthesis is still running. ``/ask/stream/demo`` is the feature-3
dogfood harness: a minimal client page that renders each event with its
arrival time, for the first-token latency recording.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app.rate_limit import limiter
from app.routers.common import _audio_url, _jsonable, _mod, _run

router = APIRouter()


class AskIn(BaseModel):
    text: str
    session_id: str | None = None


def _enforce_session_limit(session_id: str | None) -> None:
    """Feature 5: per-session fixed window over the ask surface; 429 with Retry-After when full.

    The limit comes from the model gateway's bucket config (core.llm.BUCKET_LIMITS).
    Sessionless calls share one key rather than escaping the limit via a fresh uuid each time.
    """
    key = session_id or "anonymous"
    if not limiter.check(key):
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded: {limiter.limit} requests per {limiter.window_s:g}s for this session",
            headers={"Retry-After": f"{limiter.retry_after(key):.0f}"},
        )


@router.post("/ask")
async def ask(body: AskIn):
    sid = body.session_id or str(uuid.uuid4())
    _enforce_session_limit(body.session_id)
    out = await _run("reflex", "ask", body.text, sid)
    if isinstance(out, dict):
        out.setdefault("session_id", sid)
        out["audio_url"] = _audio_url(out.get("spoken"))
    return out


def _sse_frame(event: dict) -> str:
    """One SSE frame: `event: <type>` plus a JSON data line."""
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(_jsonable(event), default=str)}\n\n"


@router.post("/ask/stream")
async def ask_stream(body: AskIn):
    sid = body.session_id or str(uuid.uuid4())
    _enforce_session_limit(body.session_id)
    stream = _mod("answer_stream")

    async def frames():
        async for event in stream.answer_stream(body.text, sid):
            yield _sse_frame(event)

    return StreamingResponse(frames(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# Feature-3 dogfood harness: a minimal streaming client served by the API itself
# (app/static is workstream G's, so this page lives here). It renders every SSE
# event with its arrival time and calls out the first-token latency — the
# <1.5 s acceptance number. Not linked from the console.
DEMO_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Mycelium — /ask/stream dogfood</title>
<style>
 body{font-family:ui-monospace,monospace;background:#0d1117;color:#c9d1d9;margin:2rem;max-width:56rem}
 h1{font-size:1.1rem;color:#58a6ff} input{width:60%;background:#161b22;color:#c9d1d9;border:1px solid #30363d;padding:.5rem}
 button{padding:.5rem 1rem;background:#238636;color:#fff;border:0;cursor:pointer}
 .ev{margin:.25rem 0;padding:.25rem .5rem;border-left:3px solid #30363d}
 .ev.t-first{border-left-color:#3fb950}.ev.t-done{border-left-color:#58a6ff}
 .ms{color:#8b949e;margin-right:.5rem}
 #answer{white-space:pre-wrap;margin:1rem 0;padding:1rem;background:#161b22;border:1px solid #30363d;min-height:3rem}
 #latency{font-size:1.2rem;color:#3fb950;min-height:1.5rem}
</style></head><body>
<h1>/ask/stream — feature 3 dogfood</h1>
<p><input id="q" value="what test runner does voiceai use" aria-label="question"> <button id="go">Ask (stream)</button></p>
<div id="latency"></div>
<div id="answer"></div>
<div id="log"></div>
<script>
var t0 = 0, firstAt = null;
function ms(){ return Math.round(performance.now() - t0); }
function logEv(name, line, cls){
  var d = document.createElement('div');
  d.className = 'ev' + (cls ? ' t-' + cls : '');
  d.textContent = ms() + 'ms  ' + name + (line ? '  ' + line : '');
  document.getElementById('log').prepend(d);
}
document.getElementById('go').onclick = async function(){
  document.getElementById('log').innerHTML = ''; document.getElementById('answer').textContent = '';
  document.getElementById('latency').textContent = ''; firstAt = null; t0 = performance.now();
  var res = await fetch('/ask/stream', {method:'POST', headers:{'content-type':'application/json'},
    body: JSON.stringify({text: document.getElementById('q').value, session_id: 'dogfood-' + Date.now()})});
  var reader = res.body.getReader(), buf = '';
  for(;;){
    var r = await reader.read(); if (r.done) break;
    buf += new TextDecoder().decode(r.value, {stream:true});
    var i;
    while((i = buf.indexOf('\\n\\n')) >= 0){
      var frame = buf.slice(0, i); buf = buf.slice(i + 2);
      var type = 'message', data = '';
      frame.split('\\n').forEach(function(l){
        if (l.startsWith('event:')) type = l.slice(6).trim();
        else if (l.startsWith('data:')) data += l.slice(5).trim();
      });
      if (!data) continue;
      var ev = JSON.parse(data);
      if (type === 'synthesis'){
        if (firstAt === null){ firstAt = ms(); document.getElementById('latency').textContent = 'first token: ' + firstAt + ' ms'; }
        document.getElementById('answer').textContent += ev.delta;
      }
      logEv(type, type === 'done' ? 'ms=' + ev.answer.ms : (ev.delta || ev.shape || ev.n_chunks != null ? JSON.stringify(ev).slice(0, 120) : ''), type === 'synthesis' ? 'first' : type);
    }
  }
  logEv('stream-end', '');
};
</script></body></html>"""


@router.get("/ask/stream/demo")
async def ask_stream_demo():
    return HTMLResponse(DEMO_PAGE)

