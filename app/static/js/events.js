import { $, glow } from "./dom.js";
import { feedAdd } from "./feed.js";
import { scheduleVitals, updateMetricsRow } from "./vitals.js";
import { renderPromote, renderGate, upsertCandidate } from "./improve.js";
import { renderLesson } from "./reflect.js";

function handleEvent(msg) {
  if (!msg || typeof msg !== "object") return;
  var row = msg.row || {};
  if (msg.table === "ledger") {
    var t = String(row.type || "");
    if (t === "promote") { renderPromote(row); scheduleVitals(); return; }
    feedAdd(row);
    if (t === "gate") { renderGate(row); }
    else if (t === "lesson") { renderLesson(row); }
    else if (t === "correct") glow($("#priorWrap"), "r");
    else if (t === "prior") glow($("#priorWrap"), "a");
    scheduleVitals();
  } else if (msg.table === "candidate") { upsertCandidate(row); scheduleVitals(); }
  else if (msg.table === "vitals") { updateMetricsRow(row); }  /* feature 64: live metrics push */
}

export { handleEvent };
