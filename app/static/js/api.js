import { clone } from "./dom.js";
import { FX } from "./fixtures.js";
import { MOCK, MOCK_VITALS } from "./mode.js";
import { state } from "./session.js";

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
async function jpostForm(path, form) {
  if (MOCK) return mockForm(path, form);
  var res = await fetch(path, { method: "POST", body: form });
  var data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw parseErr(data, res);
  return data || {};
}

/* ---- mock implementations ---- */
var mockAskCount = 0;
async function mockGet(path) {
  if (path.indexOf("/vitals") === 0) { await delay(120); return clone(MOCK_VITALS); }
  if (path.indexOf("/config") === 0) { await delay(80); return clone(FX.config); }
  throw ApiError("no mock for GET " + path, 404);
}
async function mockPost(path, body) {
  if (path === "/ask") {
    await delay(900);
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

export { jget, jpost, jpostForm };
