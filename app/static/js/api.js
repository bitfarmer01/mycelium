import { clone } from "./dom.js";
import { FX } from "./fixtures.js";
import { MOCK, MOCK_VITALS } from "./mode.js";
import { state } from "./session.js";
import { predictPrior } from "./prior_math.js";

/* ------------------------------------------------------------------------ api layer --
   One place for every request. In mock mode nothing touches the network. */
function ApiError(detail, status) {
  var e = new Error(detail || ("HTTP " + status));
  e.status = status; e.detail = detail; return e;
}
function delay(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function parseErr(data, res) {
  return ApiError((data && (data.detail || data.error)) || (res.status + " " + res.statusText), res.status);
}
async function jget(path) {
  if (MOCK) return mockGet(path);
  var res = await fetch(path, { headers: { Accept: "application/json" } });
  var data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw parseErr(data, res);
  return data || {};
}
async function jpost(path, body) {
  if (MOCK) return mockPost(path, body);
  var res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body == null ? {} : body) });
  var data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw parseErr(data, res);
  return data || {};
}
async function jput(path, body) {
  if (MOCK) return mockPut(path, body);
  var res = await fetch(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body == null ? {} : body) });
  var data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw parseErr(data, res);
  return data || {};
}
async function jpostForm(path, form) {
  if (MOCK) return mockForm(path, form);
  var res = await fetch(path, { method: "POST", body: form });
  var data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw parseErr(data, res);
  return data || {};
}

/* PR #26 background-ingest contract: POST /upload returns 202 {job_id, status, files,
   rejected} the moment files are saved; chunking/embedding/storing runs server-side and
   GET /upload/status/{job_id} flips to done (carrying the full ingest result) or failed. */
function uploadNeedsPolling(res) {
  /* Mock mode (and a legacy synchronous server) answer with the finished result
     directly: chunk totals instead of a job id. Those pass straight through. */
  return res != null && res.job_id != null && res.result == null;
}
async function pollUpload(ack, opts) {
  opts = opts || {};
  var every = opts.intervalMs || 500, cap = opts.maxMs || 2000, budget = opts.timeoutMs || 120000;
  if (!uploadNeedsPolling(ack)) return ack;
  var t0 = Date.now(), wait = every;
  for (;;) {
    await delay(wait);
    if (Date.now() - t0 > budget) throw ApiError("upload indexing timed out", 504);
    wait = Math.min(cap, wait * 2);
    var job;
    try {
      job = await jget("/upload/status/" + ack.job_id);
    } catch (e) {
      if (e && e.status === 404) throw ApiError("upload job expired before it finished", 410);
      if (Date.now() - t0 > budget) throw e; /* transient blips: keep polling to the budget */
      continue;
    }
    if (job.status === "done") return job.result || {};
    if (job.status === "failed") throw ApiError(job.error || "indexing failed", 500);
  }
}

/* ---- mock implementations ---- */
var mockAskCount = 0;
/* shared by mockPost("/ask") and mockStream: the fixture answer, with the
   cited variant when the question names a cited topic or every other ask */
function mockAnswer(body) {
  mockAskCount++;
  var base = clone(FX.ask);
  if (body && /cite|bs wisp|playbook/i.test(String(body.text || ""))) {
    base.citations_resolved = FX.askCited.citations_resolved;
    base.screen = FX.askCited.screen; base.citations = clone(FX.askCited.citations);
  } else if (mockAskCount % 2 === 0) {
    base.citations_resolved = FX.askCited.citations_resolved;
    base.screen = FX.askCited.screen; base.citations = clone(FX.askCited.citations);
  }
  return base;
}
async function mockGet(path) {
  if (path.indexOf("/vitals") === 0) { await delay(120); return clone(MOCK_VITALS); }
  if (path.indexOf("/config") === 0) { await delay(80); return clone(FX.config); }
  if (path.indexOf("/voice/prefs") === 0) { /* feature 57: demo-friendly mock preference */ await delay(60); return { user: "mock", volume: 0.8, speed: 1.0, stored: true }; }
  throw ApiError("no mock for GET " + path, 404);
}
async function mockPost(path, body) {
  if (path === "/ask") { await delay(900); return mockAnswer(body); }
  if (path === "/learning/preview") {
    await delay(160);
    var pvBefore = (state.prior && state.prior.slice()) || clone(FX.ask.prior);
    var pvRoles = ["literal", "intent", "scope"];
    var pvIdx = pvRoles.indexOf(body && body.to_role);
    var pvConf = Math.min(1, Math.max(0, body && body.confidence == null ? 1 : body.confidence));
    var pvRate = 0.3 * pvConf;
    var pvAfter = pvBefore.slice();
    if (pvIdx >= 0) pvAfter = predictPrior(pvBefore, pvIdx, pvRate);
    return { shape: "factual", from_role: "intent", to_role: body && body.to_role, confidence: pvConf,
             rate: pvRate, prior_before: pvBefore, prior_after: pvAfter,
             delta: pvIdx >= 0 ? pvAfter[pvIdx] - pvBefore[pvIdx] : 0 };
  }
  if (path === "/correct") {
    await delay(420);
    var before = (state.prior && state.prior.slice()) || clone(FX.ask.prior);
    var roles = ["literal", "intent", "scope"];
    var i = roles.indexOf(body && body.to_role);
    var after = before.slice();
    if (i >= 0) { var target = roles.map(function (_, k) { return k === i ? 1 : 0; });
      after = before.map(function (p, k) { return p + 0.3 * (target[k] - p); });
      var s = after.reduce(function (a, b) { return a + b; }, 0) || 1; after = after.map(function (p) { return p / s; }); }
    return { prior_before: before, prior_after: after, ledger_ids: ["ledger:correct-mock"], lesson_id: "lesson:mock" };
  }
  if (path === "/confirm") { await delay(260); return { ok: true }; }
  if (path === "/tick") { await delay(260); return { decayed: 2 }; }
  if (path === "/reflect") { await delay(700); return clone(FX.reflect); }
  if (path === "/improve") {
    await delay(950);
    var cands = clone(FX.candidates).concat(clone(FX.scored));
    return { candidates: cands, slot_version: MOCK_VITALS.slot_version, baseline: null };
  }
  if (path === "/stt") { await delay(420); return { text: "how do we run a discovery call?" }; }
  throw ApiError("no mock for POST " + path, 404);
}
/* feature 69: the ask path over /ask/stream — same answer payload as POST /ask
   (the stream's `done` event carries it whole) plus per-stage events whose
   cumulative `ms` feeds the answer timeline. onEvent gets every parsed event;
   the promise resolves with the answer payload from `done`. */
async function streamAsk(text, sessionId, onEvent) {
  if (MOCK) return mockStream(text, sessionId, onEvent);
  var res = await fetch("/ask/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: text, session_id: sessionId == null ? null : sessionId }) });
  if (!res.ok) { var d = null; try { d = await res.json(); } catch (e) {} throw parseErr(d, res); }
  var reader = res.body.getReader(), dec = new TextDecoder(), buf = "", answer = null;
  for (;;) {
    var chunk = await reader.read();
    if (chunk.done) break;
    buf += dec.decode(chunk.value, { stream: true });
    var idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      var frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
      var ev = parseSseFrame(frame);
      if (!ev) continue;
      if (ev.type === "done") answer = ev.answer;
      if (onEvent) onEvent(ev);
    }
  }
  if (!answer) throw ApiError("stream ended without an answer", 502);
  return answer;
}
function parseSseFrame(frame) {
  var type = "message", data = "";
  frame.split("\n").forEach(function (line) {
    if (line.indexOf("event:") === 0) type = line.slice(6).trim();
    else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
  });
  if (!data) return null;
  try { var ev = JSON.parse(data); ev.type = ev.type || type; return ev; }
  catch (e) { return null; }
}
/* mock stream: the same stage shape as core.answer_stream, on a clock —
   readings resolve, then retrieval, then synthesis deltas, then done */
function mockStream(text, sessionId, onEvent) {
  var events = [
    { type: "start", ms: 42 },
    { type: "readings", ms: 684, shape: FX.ask.shape, taken: FX.ask.taken, readings: clone(FX.ask.readings) },
    { type: "context", ms: 1291, n_chunks: 2, citations: clone(FX.askCited.citations) },
    { type: "synthesis", delta: "…", ms: 1712 },
    { type: "synthesis", delta: "…", ms: 2140 },
  ];
  return new Promise(function (resolve, reject) {
    var i = 0;
    (function next() {
      if (i >= events.length) {
        setTimeout(function () {
          var answer = mockAnswer({ text: text });
          onEvent({ type: "done", answer: answer, ms: answer.ms });
          resolve(answer);
        }, 260);
        return;
      }
      var ev = events[i++];
      setTimeout(function () { onEvent(ev); next(); }, i * 380);
    })();
  });
}

async function mockForm(path, form) {
  if (path !== "/upload") throw ApiError("no mock for POST " + path, 404);
  var names = [];
  try { form.forEach(function (v, k) { if (k === "files" && v && v.name) names.push(v.name); }); } catch (e) {}
  await delay(900);
  if (names.some(function (n) { return /\.exe$|\.zip$/i.test(n); })) return clone(FX.uploadRejected);
  if (names.some(function (n) { return /huge|toobig/i.test(n); })) throw ApiError(FX.uploadErrors["413"].detail, 413);
  if (names.some(function (n) { return /busy|locked/i.test(n); })) throw ApiError(FX.uploadErrors["429"].detail, 429);
  var out = { files: [], rejected: [], chunks: 0, ms: 1400 };
  names.forEach(function (n) {
    var kind = (n.split(".").pop() || "txt").toLowerCase();
    var chunks = 4 + (n.length % 9) * 2;
    out.files.push({ name: n, kind: kind === "markdown" ? "md" : kind, chunks: chunks, title: n.replace(/\.[^.]+$/, "") });
    out.chunks += chunks;
  });
  MOCK_VITALS.counts.chunk = (MOCK_VITALS.counts.chunk || 0) + out.chunks;
  MOCK_VITALS.counts.candidate = (MOCK_VITALS.counts.candidate || 0) + 2;
  return out;
}

/* Feature 57: mock mode echoes the prefs save so demos never hit the network. */
function mockPut(path, body) {
  if (path === "/voice/prefs") {
    return { user: (body && body.user) || "default", volume: Number(body && body.volume), speed: Number(body && body.speed), stored: true };
  }
  throw ApiError("no mock for PUT " + path, 404);
}

export { jget, jpost, jput, jpostForm, pollUpload, streamAsk };
