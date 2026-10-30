import { clone } from "./dom.js";
import { FX } from "./fixtures.js";
import { MOCK_VITALS } from "./mode.js";
import { state } from "./session.js";
import { setStatus } from "./status.js";
import { speak, browserSpeak } from "./audio.js";
import { startPipeline, stopPipeline } from "./pipeline.js";
import { renderAnswer } from "./answer.js";
import { travelPrior } from "./prior.js";
import { handleEvent } from "./events.js";
import { renderReflect } from "./reflect.js";
import { renderImprove, upsertCandidate } from "./improve.js";
import { renderTickResult } from "./tick.js";
import { renderVitals, bumpCorpus } from "./vitals.js";
import { renderUploadResult, renderClientRejection, renderUploadError } from "./upload.js";
import { applyTranscript } from "./talk.js";
import { setWs } from "./ws.js";

/* ============================================================================ audit --
   ?mock=1&audit=1 drives every entry of the event→feedback map through the real render
   path, watches the entry's region with a MutationObserver, and reports any entry that
   produced no visible change within 1000 ms. */
var auditSeq = 0, auditFinished = false;
function ledgerRow(type) {
  var found = null;
  FX.ledger.forEach(function (r) { if (!found && r.type === type) found = r; });
  var row = clone(found || { type: type }); auditSeq++;
  row.id = (row.id || ("ledger:" + type)) + ":audit" + auditSeq;
  row.ts = new Date().toISOString();
  return row;
}
function mockImprove() { return { candidates: clone(FX.candidates).concat(clone(FX.scored)), slot_version: "1.0", baseline: null }; }
function ensureVitals() { if (!state.vitals) state.vitals = clone(MOCK_VITALS); }

var AUDIT_MAP = [
  { key: "ask.start", region: "#pipeline", run: function () { startPipeline("how do we run a discovery call?"); setTimeout(stopPipeline, 60); } },
  { key: "ask.answered", region: "#answer", run: function () { renderAnswer(clone(FX.ask)); } },
  { key: "ask.cited_n_n", region: "#citeBadge", run: function () { renderAnswer(clone(FX.askCited)); } },
  { key: "ask.abstain_0_0", region: "#citeBadge", run: function () { renderAnswer(clone(FX.ask)); } },
  { key: "reading.taken", region: "#chips", run: function () { renderAnswer(clone(FX.askCited)); } },
  { key: "reading.correct", region: "#prior", run: function () { travelPrior([0.33, 0.34, 0.33], [0.2, 0.53, 0.27]); } },
  { key: "ledger.confirm", region: "#feed", run: function () { handleEvent({ table: "ledger", action: "CREATE", row: ledgerRow("confirm") }); } },
  { key: "ledger.correct", region: "#feed", run: function () { handleEvent({ table: "ledger", action: "CREATE", row: ledgerRow("correct") }); } },
  { key: "ledger.prior", region: "#priorWrap", run: function () { handleEvent({ table: "ledger", action: "CREATE", row: ledgerRow("prior") }); } },
  { key: "ledger.decay", region: "#feed", run: function () { handleEvent({ table: "ledger", action: "CREATE", row: ledgerRow("decay") }); } },
  { key: "ledger.veto", region: "#feed", run: function () { handleEvent({ table: "ledger", action: "CREATE", row: ledgerRow("veto") }); } },
  { key: "ledger.gate", region: "#candidates", run: function () { handleEvent({ table: "ledger", action: "CREATE", row: ledgerRow("gate") }); } },
  { key: "ledger.promote", region: "#slotBadge", run: function () { handleEvent({ table: "ledger", action: "CREATE", row: ledgerRow("promote") }); } },
  { key: "ledger.lesson", region: "#sentences", run: function () { handleEvent({ table: "ledger", action: "CREATE", row: ledgerRow("lesson") }); } },
  { key: "reflect.result", region: "#sentences", run: function () { renderReflect(clone(FX.reflect)); } },
  { key: "improve.result", region: "#candidates", run: function () { renderImprove(mockImprove()); } },
  { key: "candidate.vetoed_prelint", region: "#candidates", run: function () { upsertCandidate(clone(FX.candidates[3])); } },
  { key: "candidate.scored_pass", region: "#candidates", run: function () { upsertCandidate(clone(FX.scored[0])); } },
  { key: "candidate.scored_veto", region: "#candidates", run: function () { upsertCandidate(clone(FX.scored[1])); } },
  { key: "candidate.clean_awaiting_baseline", region: "#candidates", run: function () { upsertCandidate(clone(FX.candidates[0])); } },
  { key: "candidate.error", region: "#candidates", run: function () { upsertCandidate(clone(FX.scored[2])); } },
  { key: "verdict.pass", region: "#candidates", run: function () { renderImprove({ candidates: [clone(FX.scored[0])], baseline: clone(FX.baseline) }); } },
  { key: "verdict.veto", region: "#candidates", run: function () { renderImprove({ candidates: [clone(FX.scored[1])], baseline: clone(FX.baseline) }); } },
  { key: "tick.response", region: "#feed", run: function () { renderTickResult({ decayed: 2 }); } },
  { key: "vitals.update", region: "#vitalsBody", run: function () { ensureVitals(); renderVitals(true); } },
  { key: "config.load", region: "#vitalsBody", run: function () { state.config = clone(FX.config); renderVitals(false); } },
  { key: "upload.ok", region: "#uploadFiles", run: function () { renderUploadResult({ files: [{ name: "Team_Handbook.pdf", kind: "pdf", chunks: 14, title: "Team Handbook" }], rejected: [], chunks: 14, ms: 6200 }); } },
  { key: "upload.rejected_file", region: "#uploadRejected", run: function () { renderUploadResult(clone(FX.uploadRejected)); } },
  { key: "upload.precheck", region: "#uploadRejected", run: function () { renderClientRejection([{ name: "budget.rtf", reason: "unsupported type (md, txt, html, pdf, csv, tsv, xlsx)" }]); } },
  { key: "upload.413", region: "#uploadError", run: function () { renderUploadError(413, FX.uploadErrors["413"].detail, false); } },
  { key: "upload.429", region: "#uploadError", run: function () { renderUploadError(429, FX.uploadErrors["429"].detail, true); } },
  { key: "upload.corpus_tick", region: "#vitalsBody", run: function () { ensureVitals(); bumpCorpus(14); } },
  { key: "stt.transcript", region: "#status", run: function () { applyTranscript("how do we run a discovery call?"); } },
  { key: "tts.play", region: "#audioState", run: function () { speak("The wiki does not cover this.", null); } },
  { key: "tts.fallback", region: "#audioState", run: function () { browserSpeak("The wiki does not cover this."); } },
  { key: "ws.down", region: "#wsDot", run: function () { setWs(false); } },
  { key: "ws.up", region: "#wsDot", run: function () { setWs(true); } },
  { key: "error.ask", region: "#status", run: function () { setStatus("ask failed: network unreachable", "err"); } },
  { key: "error.correct", region: "#status", run: function () { setStatus("correct failed: to_role must be one of literal, intent, scope", "err"); } },
  { key: "error.confirm", region: "#status", run: function () { setStatus("confirm failed: connection reset by peer", "err"); } },
  { key: "error.tick", region: "#status", run: function () { setStatus("tick failed: db unavailable", "err"); } },
  { key: "error.reflect", region: "#status", run: function () { setStatus("reflect failed: model timeout after 30 s", "err"); } },
  { key: "error.improve", region: "#status", run: function () { setStatus("improve failed: sandbox runner offline", "err"); } },
  { key: "error.stt", region: "#status", run: function () { setStatus("transcription failed: speech-to-text unavailable", "err"); } },
  { key: "error.upload", region: "#uploadError", run: function () { renderUploadError(503, "database unavailable: ConnectionError", false); } },
  { key: "citations.0_0", region: "#citeBadge", run: function () { renderAnswer(clone(FX.ask)); } }
];

function auditCase(entry) {
  return new Promise(function (resolve) {
    var region = null;
    try { region = document.querySelector(entry.region); } catch (e) {}
    if (!region) { resolve(false); return; }
    var done = false, obs = null, tmr = null;
    function finish(ok) {
      if (done) return; done = true;
      if (obs) { try { obs.disconnect(); } catch (e) {} }
      clearTimeout(tmr); resolve(ok);
    }
    try {
      obs = new MutationObserver(function () { finish(true); });
      obs.observe(region, { subtree: true, childList: true, characterData: true, attributes: true });
    } catch (e) { resolve(false); return; }
    tmr = setTimeout(function () { finish(false); }, 1000);
    try { var r = entry.run(); if (r && typeof r.then === "function") r.then(null, function () {}); } catch (e) {}
  });
}
function finishAudit(fails) {
  if (auditFinished) return;
  auditFinished = true;
  var text = fails.length ? ("AUDIT FAIL: " + fails.join(", ")) : "AUDIT PASS";
  document.title = text;
  var pre = document.getElementById("audit");
  if (pre) { pre.hidden = false; pre.textContent = text; }
}
function runAudit() {
  var fails = [], i = 0;
  setTimeout(function () { finishAudit(fails); }, 14000);
  (function next() {
    if (i >= AUDIT_MAP.length) { finishAudit(fails); return; }
    var entry = AUDIT_MAP[i++];
    auditCase(entry).then(function (ok) { if (!ok) fails.push(entry.key); next(); });
  })();
}

export { runAudit };
