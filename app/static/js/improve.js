import { $, el, num, signed, meanOf, glow } from "./dom.js";
import { state } from "./session.js";
import { jpost } from "./api.js";
import { setStatus } from "./status.js";
import { feedAdd } from "./feed.js";
import { scheduleVitals } from "./vitals.js";
import { FX } from "./fixtures.js";

/* ------------------------------------------------------------------------- improve -- */
function showImprove() { $("#improveBox").hidden = false; var w = $("#candidates"); if (w.firstChild && w.firstChild.classList && w.firstChild.classList.contains("empty")) w.textContent = ""; }
function prependCard(node) { var w = $("#candidates"); w.insertBefore(node, w.firstChild); }
function bandEl(cand, baseline) {
  var wrap = el("div", "band");
  var bm = baseline ? meanOf(baseline.means) : null;
  if (!baseline || bm == null) { wrap.appendChild(el("div", "cline", "designed, measured after the demo")); return wrap; }
  var bs = baseline.spread == null ? 0 : Number(baseline.spread);
  var cm = cand.mean == null ? bm : Number(cand.mean);
  var pass = cand.verdict && /pass/i.test(String(cand.verdict));
  var lo = Math.min(bm - bs, cm), hi = Math.max(bm + bs, cm);
  var pad = (hi - lo) * 0.3 || 0.05; lo -= pad; hi += pad;
  var pos = function (x) { return Math.max(0, Math.min(100, ((x - lo) / (hi - lo)) * 100)); };
  var track = el("div", "band-track");
  var range = el("div", "band-range"); range.style.left = pos(bm - bs) + "%"; range.style.width = Math.max(1.5, pos(bm + bs) - pos(bm - bs)) + "%"; track.appendChild(range);
  var base = el("div", "band-base"); base.style.left = pos(bm) + "%"; track.appendChild(base);
  var dot = el("div", "band-dot"); dot.style.left = pos(cm) + "%"; track.appendChild(dot);
  wrap.appendChild(track);
  var leg = el("div", "band-legend");
  leg.appendChild(document.createTextNode("baseline " + num(bm, 2) + " ± " + bs.toFixed(2) + " · candidate "));
  leg.appendChild(el("span", pass ? "g" : "r", num(cm, 2)));
  leg.appendChild(document.createTextNode(" · " + signed(cand.delta, 2) + " "));
  leg.appendChild(el("span", pass ? "g" : "r", String(cand.verdict || "")));
  wrap.appendChild(leg);
  return wrap;
}
function candidateCard(c, baseline) {
  var status = String(c.status || "");
  var kind;
  if (/vetoed_prelint/.test(status)) kind = "block";
  else if (/error/.test(status)) kind = "error";
  else if (/clean_awaiting_baseline/.test(status)) kind = "pending";
  else if (c.verdict && /pass/i.test(String(c.verdict))) kind = "pass";
  else if (c.verdict && /veto/i.test(String(c.verdict))) kind = "veto";
  else if (/scored/.test(status)) kind = "veto";
  else kind = "pending";
  var tag = kind === "block" ? "BLOCK" : kind === "pass" ? "PASS" : kind === "veto" ? "VETO" : kind === "error" ? "ERROR" : "PENDING";
  var card = el("div", "cand " + kind);
  var head = el("div", "chead");
  head.appendChild(el("span", "tag", tag));
  head.appendChild(el("span", "cname", c.name == null ? "(unnamed)" : String(c.name)));
  if (c.axis) head.appendChild(el("span", "caxis", "axis:" + c.axis));
  card.appendChild(head);
  if (kind === "block") {
    var r = el("div", "creason", "vetoed before any sandbox — matched ");
    r.appendChild(el("span", "pat", c.veto_reason ? String(c.veto_reason) : "lint pattern"));
    card.appendChild(r);
  } else if (kind === "error") {
    card.appendChild(el("div", "creason", c.veto_reason ? String(c.veto_reason) : "candidate failed"));
  } else if (kind === "pending") {
    card.appendChild(el("div", "cline", "awaiting baseline"));
  } else {
    card.appendChild(bandEl(c, baseline));
  }
  var foot = el("div", "cfoot");
  ["status", "verdict", "spread", "ratio", "sandbox_id", "runner"].forEach(function (k) { if (c[k] == null || c[k] === "") return; foot.appendChild(el("span", null, k + " " + c[k])); });
  if (foot.children.length) card.appendChild(foot);
  return card;
}
function candidateKey(c) { return String(c.sandbox_id || c.id || c.name || ""); }
var candCards = {};
function renderImprove(res) {
  var wrap = $("#candidates"); wrap.textContent = ""; state.candidates = {}; candCards = {};
  var list = res.candidates || [];
  $("#improveMeta").textContent = list.length + " candidates" + (res.baseline == null ? " · baseline pending" : "");
  list.forEach(function (c) { var k = candidateKey(c); state.candidates[k] = c; var card = candidateCard(c, res.baseline); candCards[k] = card; wrap.appendChild(card); });
  $("#improveBox").hidden = false;
  if (!list.length) wrap.appendChild(el("p", "empty", "no candidates"));
  glow($("#improveBox"), "b");
}
function upsertCandidate(row) {
  if (!row || typeof row !== "object") return;
  var k = candidateKey(row); state.candidates[k] = row;
  if (!$("#improveBox").hidden) {
    showImprove();
    var card = candidateCard(row, null);
    var old = candCards[k];
    if (old && old.parentNode) old.parentNode.replaceChild(card, old);
    else prependCard(card);
    candCards[k] = card;
    glow(card, /veto|block/i.test(String(row.status)) ? "r" : "g");
  }
}
function renderGate(row) {
  showImprove();
  var pass = Number(row.delta) > 0;
  var cand = { name: row.target || "gate", axis: "gate", status: "scored", verdict: pass ? "PASS" : "VETO", mean: row.after, spread: null, delta: row.delta, ratio: null, sandbox_id: "sbx_gate", runner: (state.vitals && state.vitals.runner) || "daytona" };
  var card = candidateCard(cand, FX.baseline);
  prependCard(card); glow(card, pass ? "g" : "r");
  setStatus("gate · delta " + signed(row.delta, 2) + " vs spread " + (row.cause || "").replace(/.*spread\s*/i, "") + " → " + cand.verdict, pass ? "ok" : "err");
}
function renderPromote(row) {
  var v = row.after != null ? row.after : (state.vitals && state.vitals.slot_version);
  if (state.vitals) state.vitals.slot_version = v;
  var b = $("#slotBadge"); b.textContent = "slot " + v; b.className = "pill mono g"; glow(b, "g");
  feedAdd({ id: row.id || ("ledger:promote:" + Date.now().toString(36)), type: "promote", target: row.target || "slot", target_kind: "slot", before: row.before, after: row.after, delta: row.delta, cause: row.cause || "promoted slot", ts: new Date().toISOString() });
  setStatus("slot promoted → v" + v + " (only after a PASS)", "ok");
}
async function improve() {
  setStatus("improving… (sandboxes may take a while)", "busy");
  try {
    var res = await jpost("/improve", {});
    renderImprove(res);
    if (res.slot_version != null && res.slot_version !== state.prevSlot) { state.prevSlot = res.slot_version; renderPromote({ target: "slot", after: res.slot_version, cause: "slot version advanced", before: res.slot_version }); }
    var blocked = (res.candidates || []).filter(function (c) { return /vetoed_prelint/.test(String(c.status)); }).length;
    var scored = (res.candidates || []).filter(function (c) { return c.verdict; }).length;
    setStatus("candidates " + (res.candidates || []).length + " · blocked " + blocked + " · scored " + scored + (res.baseline == null ? " · baseline measured after the demo" : ""), "ok");
    scheduleVitals();
  } catch (e) { setStatus("improve failed: " + e.message, "err"); }
}

$("#improveBtn").addEventListener("click", improve);

export { renderImprove, upsertCandidate, renderGate, renderPromote };
