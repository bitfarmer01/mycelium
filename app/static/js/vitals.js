import { $, el, countUp, clone, fmtInt, num, meanOf, glow } from "./dom.js";
import { state } from "./session.js";
import { jget } from "./api.js";

/* --------------------------------------------------------------------- vitals panel -- */
function renderVitals(animate) {
  var v = state.vitals; if (!v) return;
  var box = $("#vitalsBody"); box.textContent = "";
  $("#vitalsWhen").textContent = "live";

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
async function loadVitals(animate) {
  try {
    var v = await jget("/vitals");
    state.vitals = v;
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

export { renderVitals, loadVitals, loadConfig, scheduleVitals, bumpCorpus };
