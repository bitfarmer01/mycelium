import { $, el, countUp, clone, fmtInt, num, meanOf, glow } from "./dom.js";
import { state } from "./session.js";
import { jget } from "./api.js";

/* --------------------------------------------------------------------- vitals panel -- */
function fmtMs(ms) {
  if (ms == null || !isFinite(ms)) return "—";
  return ms >= 10000 ? (ms / 1000).toFixed(1) + "s" : Math.round(ms) + "ms";
}
/* Feature 64: p50/p95 latency and error rate, one cell each; corpus size already
   renders in the counts grid below. Absent fields show "—" (fail-soft contract). */
function metricCell(grid, label, txt, cls) {
  var cell = el("div", "vcell" + (cls ? " " + cls : ""));
  cell.appendChild(el("div", "k", label));
  cell.appendChild(el("div", "v", txt));
  grid.appendChild(cell);
}
function renderMetrics() {
  var m = (state.vitals || {}).metrics;
  var box = $("#vitalsBody");
  var grid = el("div", "vgrid");
  var err = m && m.error_rate;
  metricCell(grid, "p50", fmtMs(m && m.latency_ms_p50));
  metricCell(grid, "p95", fmtMs(m && m.latency_ms_p95));
  metricCell(grid, "errors", err == null ? "—" : (err * 100).toFixed(err > 0.005 ? 1 : 0) + "%", err > 0.05 ? "hot" : "");
  box.appendChild(grid);
}
function renderVitals(animate) {
  var v = state.vitals; if (!v) return;
  var box = $("#vitalsBody"); box.textContent = "";
  $("#vitalsWhen").textContent = "live";

  renderMetrics();

  var slotRow = el("div", "vrow");
  var slot = el("span", "pill mono b");
  slot.appendChild(el("b", null, "slot " + (v.slot_version == null ? "—" : v.slot_version)));
  slotRow.appendChild(slot);
  if (v.slot_hash) { var hs = el("span", "pill mono k", "hash " + String(v.slot_hash).slice(0, 10)); slotRow.appendChild(hs); }
  if (v.runner) slotRow.appendChild(el("span", "pill mono k", "runner " + v.runner));
  box.appendChild(slotRow);

  var baseRow = el("div", "vrow");
  baseRow.appendChild(el("span", "vsub", "baseline"));
  var b = v.baseline;
  if (!b) baseRow.appendChild(el("span", "hint", "designed, measured after the demo"));
  else {
    var m = meanOf(b.means), s = (b.spread == null) ? null : Number(b.spread);
    var txt = (m == null ? "—" : num(m, 2)) + " ± " + (s == null || !isFinite(s) ? "—" : s.toFixed(2));
    baseRow.appendChild(el("span", "mono", txt));
  }
  box.appendChild(baseRow);

  var c = v.counts || {};
  var grid = el("div", "vgrid");
  var prev = state.lastCounts || {};
  [["chunk", "corpus"], ["question", "questions"], ["lesson", "lessons"], ["candidate", "candidates"]].forEach(function (p) {
    var cell = el("div", "vcell" + (animate && prev[p[0]] != null && c[p[0]] != null && c[p[0]] > prev[p[0]] ? " hot" : ""));
    cell.appendChild(el("div", "k", p[1]));
    var val = el("div", "v");
    cell.appendChild(val);
    grid.appendChild(cell);
    var from = (prev[p[0]] == null) ? (c[p[0]] || 0) : prev[p[0]];
    countUp(val, from, c[p[0]], fmtInt, animate ? 720 : 0);
  });
  box.appendChild(grid);
  state.lastCounts = clone(c);

  if (state.config) {
    var cfg = state.config, wrap = el("div", "");
    wrap.appendChild(el("div", "vsub", "constants"));
    var cs = el("div", "consts");
    [["decay", cfg.decay], ["prior_rate_correct", cfg.prior_rate_correct], ["prior_rate_uncorrected", cfg.prior_rate_uncorrected],
     ["prior_floor", cfg.prior_floor], ["commit", cfg.commit], ["margin", cfg.margin], ["slot_token_ratio", cfg.slot_token_ratio], ["knn_k", cfg.knn_k]]
      .forEach(function (p) {
        var chip = el("span", "const");
        chip.appendChild(el("b", null, String(p[0])));
        chip.appendChild(document.createTextNode(" " + p[1]));
        cs.appendChild(chip);
      });
    wrap.appendChild(cs);
    box.appendChild(wrap);
  }
}
/* Feed-driven update (feature 64): merge a /events vitals row into the panel.
   Ignored until the first /vitals poll has rendered — the poll carries the
   same metrics block, so nothing is lost by waiting. */
function updateMetricsRow(row) {
  var m = row && row.metrics;
  if (!m || !state.vitalsLoaded) return;
  state.vitals.metrics = m;
  renderVitals(false);
}

async function loadVitals(animate) {
  try {
    var v = await jget("/vitals");
    state.vitals = v;
    state.vitalsLoaded = true;
    if (state.prevSlot == null) state.prevSlot = v.slot_version;
    renderVitals(!!animate);
  } catch (e) {
    var box = $("#vitalsBody");
    if (!box.children.length) box.appendChild(el("p", "empty", "vitals unavailable"));
  }
}
async function loadConfig() {
  try { state.config = await jget("/config"); renderVitals(false); } catch (e) {}
}
var vitalsPending = null;
function scheduleVitals() {
  if (vitalsPending) return;
  vitalsPending = setTimeout(function () { vitalsPending = null; loadVitals(true); }, 900);
}
/* corpus counter ticks up by delta (e.g. after an upload) without waiting for a poll */
function bumpCorpus(delta) {
  if (!state.vitals) { loadVitals(true); return; }
  var c = state.vitals.counts || {};
  c.chunk = (c.chunk || 0) + Number(delta || 0);
  state.vitals.counts = c;
  renderVitals(true);
  glow($(".vcell"), "g");
}

export { renderVitals, loadVitals, loadConfig, scheduleVitals, bumpCorpus, updateMetricsRow };
