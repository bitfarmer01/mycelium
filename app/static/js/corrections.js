import { $, $$, el, num, countUp, glow } from "./dom.js";
import { state } from "./session.js";
import { jpost } from "./api.js";
import { setStatus } from "./status.js";
import { travelPrior, setPrior } from "./prior.js";
import { feedAdd, feedEl } from "./feed.js";
import { scheduleVitals } from "./vitals.js";

function chipFor(r, taken, flipText) {
  var chip = el("button", "chip" + (taken ? " taken" : ""));
  chip.type = "button";
  if (taken) chip.setAttribute("aria-current", "true");
  var head = el("div", "chead");
  head.appendChild(el("b", null, String(r.role == null ? "?" : r.role)));
  head.appendChild(document.createTextNode(taken ? " · taken" : " · tap to correct"));
  chip.appendChild(head);
  chip.appendChild(el("div", "cbody", String(r.paraphrase == null ? "" : r.paraphrase)));
  chip.appendChild(el("div", "cnum", "p " + num(r.p, 2) + " · posterior " + num(r.posterior, 2)));
  if (taken) {
    /* aria-disabled instead of disabled: the taken chip must stay focusable so
       the flip_condition tooltip is keyboard-operable (feature 63) */
    chip.setAttribute("aria-disabled", "true");
    chip.title = "the reading Mycelium took";
    if (flipText) {
      var tip = el("span", "tip");
      tip.id = "tip-" + String(r.role == null ? "x" : r.role).replace(/[^a-z0-9-]/gi, "");
      tip.setAttribute("role", "tooltip");
      tip.appendChild(el("b", null, "flips the reading if"));
      tip.appendChild(document.createTextNode(String(flipText)));
      chip.setAttribute("aria-describedby", tip.id);
      chip.appendChild(tip);
    }
  }
  else { chip.title = "correct to this reading"; chip.addEventListener("click", function () { correct(r.role); }); }
  return chip;
}
function renderChips(res) {
  var wrap = $("#chips"); wrap.textContent = "";
  (res.readings || []).forEach(function (r) {
    var taken = String(r.role) === String(res.taken);
    wrap.appendChild(chipFor(r, taken, taken ? res.flip_condition : null));
  });
  if (!wrap.children.length) wrap.appendChild(el("span", "hint", "no readings"));
}
function markCorrecting(role) {
  $$("#chips .chip").forEach(function (c) { c.classList.remove("correcting"); if (c.querySelector(".chead b") && c.querySelector(".chead b").textContent === role) c.classList.add("correcting"); });
}

/* --------------------------------------------------------------------- corrections -- */
function addCorrectFeedRow(toRole) {
  var row = { id: "ledger:correct:" + Date.now().toString(36), type: "correct", target: "shape", target_kind: "shape", before: 0.333, after: 0.533, delta: 0.2, cause: "user corrected the reading → " + toRole, ts: new Date().toISOString() };
  feedAdd(row); glow(feedEl.firstChild, "r");
}
async function correct(toRole) {
  var q = state.ask;
  if (!q || q.question_id == null) { setStatus("ask something first", "err"); return; }
  markCorrecting(toRole); setStatus("correcting → " + toRole + " …", "busy");
  try {
    var res = await jpost("/correct", { question_id: q.question_id, to_role: toRole });
    if (res.prior_before && res.prior_after) travelPrior(res.prior_before, res.prior_after);
    else if (res.prior_after) { setPrior(res.prior_after, true); glow($("#priorWrap"), "a"); }
    state.prior = res.prior_after || state.prior;
    q.taken = toRole;
    (q.readings || []).forEach(function (r) { if (String(r.role) === String(toRole)) r.posterior = 1; });
    renderChips(q); addCorrectFeedRow(toRole);
    var ids = (res.ledger_ids || []).join(", ");
    setStatus("correction recorded — the prior moved" + (ids ? " · ledger " + ids : ""), "ok");
    scheduleVitals();
  } catch (e) { setStatus("correct failed: " + e.message, "err"); }
}
function confirmViaFeed() {
  var q = state.ask;
  var row = { id: "ledger:confirm:" + ((q && q.question_id) || Date.now()), type: "confirm", target: (q && q.citations && q.citations[0] && q.citations[0].chunk_id) || "answer", target_kind: "evidence", before: 0.5, after: 0.667, delta: 0.167, cause: "user confirmed the cited answer", ts: new Date().toISOString() };
  feedAdd(row); glow(feedEl.firstChild, "g");
  var taken = $("#chips .chip.taken .cnum");
  if (taken) countUp(taken, 0.45, 1, function (v) { return "p " + num(v, 2) + " · posterior " + num(v, 2); }, 620);
}
async function confirmAnswer() {
  var q = state.ask;
  if (!q || q.question_id == null) { setStatus("ask something first", "err"); return; }
  setStatus("confirming…", "busy");
  try { await jpost("/confirm", { question_id: q.question_id }); confirmViaFeed(); setStatus("confirmed — evidence +1, posterior → 1", "ok"); scheduleVitals(); }
  catch (e) { setStatus("confirm failed: " + e.message, "err"); }
}

$("#confirmBtn").addEventListener("click", confirmAnswer);

export { renderChips, correct, confirmAnswer };
