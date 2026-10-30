import { $, el, num } from "./dom.js";

/* ------------------------------------------------------------------------ timeline --
   feature 69: answer timeline. Steps are the real pipeline stages in execution
   order — readings → retrieval → synthesis (the taken reading's query drives
   retrieval, so readings run first; the spec's stage list names the same three).
   Timings come from the server, not the wall clock: each /ask/stream stage
   event carries its cumulative `ms`, and the `done` event's answer payload
   pins the total. Any timing that is absent renders as "—" rather than a
   guess; the module renders whatever arrives and never blocks the answer. */

var STEPS = [
  { key: "readings", label: "readings" },
  { key: "context", label: "retrieval" },
  { key: "synthesis", label: "synthesis" },
];

var nodes = {};

function mark(key, cls, ms) {
  var row = nodes[key];
  if (!row) return;
  row.className = "tlstep " + cls;
  row.querySelector(".tms").textContent = ms == null ? "—" : num(ms) + " ms";
}
function reset() {
  var t = $("#timeline");
  t.textContent = "";
  t.hidden = false;
  nodes = {};
  t.appendChild(el("div", "tlhead", "answer pipeline"));
  STEPS.forEach(function (s) {
    var row = el("div", "tlstep pending");
    row.appendChild(el("span", "dot"));
    row.appendChild(el("span", "tlname", s.label));
    row.appendChild(el("span", "tms", "—"));
    t.appendChild(row);
    nodes[s.key] = row;
  });
  nodes.total = el("div", "tlstep total");
  nodes.total.appendChild(el("span", "tlname", "total"));
  nodes.total.appendChild(el("span", "tms", "—"));
  t.appendChild(nodes.total);
}

/* the ask is running: first stage goes live */
function startTimeline() {
  reset();
  mark("readings", "live", null);
}

/* one /ask/stream stage event; unknown stage types are ignored so a newer
   server cannot break an older console */
function stageEvent(ev) {
  var key = ev && ev.type;
  if (!nodes[key]) return;
  if (!nodes[key].classList.contains("done")) mark(key, "done", ev.ms);
  var i = STEPS.findIndex(function (s) { return s.key === key; });
  if (i >= 0 && i + 1 < STEPS.length) {
    var next = STEPS[i + 1].key;
    if (nodes[next].classList.contains("pending")) mark(next, "live", null);
  }
}

/* done: the answer payload's `ms` is the authoritative total; any stage the
   stream never reported stays at "—" */
function finishTimeline(totalMs) {
  STEPS.forEach(function (s) {
    if (nodes[s.key].classList.contains("pending")) mark(s.key, "done", null);
  });
  nodes.total.querySelector(".tms").textContent = totalMs == null ? "—" : num(totalMs) + " ms";
}
function failTimeline() {
  var live = STEPS.map(function (s) { return s.key; }).filter(function (k) {
    return nodes[k] && nodes[k].classList.contains("live");
  });
  if (live.length) mark(live[0], "failed", null);
  else if (nodes.total) nodes.total.classList.add("failed");
}

export { startTimeline, stageEvent, finishTimeline, failTimeline };
