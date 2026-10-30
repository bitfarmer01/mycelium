import { $, el, clamp01, setVar, countUp, num, glow } from "./dom.js";

/* ---------------------------------------------------------------------- prior bars -- */
var ROLE_ORDER = ["literal", "intent", "scope"];
var priorEls = {};
function ensurePrior() {
  var wrap = $("#prior");
  if (wrap.children.length === ROLE_ORDER.length) return;
  wrap.textContent = ""; priorEls = {};
  ROLE_ORDER.forEach(function (role) {
    var row = el("div", "pbar");
    row.appendChild(el("span", "plabel", role));
    var track = el("div", "ptrack"); var fill = el("div", "pfill");
    track.appendChild(fill); row.appendChild(track);
    var n = el("span", "pnum", "—"); row.appendChild(n);
    wrap.appendChild(row);
    priorEls[role] = { fill: fill, num: n, value: null };
  });
}
function setPrior(values, animate) {
  ensurePrior();
  ROLE_ORDER.forEach(function (role, i) {
    var v = Array.isArray(values) ? clamp01(values[i]) : 0;
    var ref = priorEls[role];
    setVar(ref.fill, "--p", v);
    var from = ref.value == null ? v : ref.value;
    countUp(ref.num, from, v, function (x) { return num(x, 3); }, animate ? 640 : 0);
    ref.value = v;
  });
}
/* the correction travel: snap to before, then animate to after */
function travelPrior(before, after) {
  setPrior(before, false);
  requestAnimationFrame(function () { setPrior(after, true); });
  glow($("#priorWrap"), "a");
  var moved = ROLE_ORDER.map(function (r, i) {
    var a = Array.isArray(after) ? clamp01(after[i]) : 0;
    return r + " " + num(a, 3);
  }).join(" · ");
  $("#priorHint").textContent = "moved → " + moved;
}

export { setPrior, travelPrior };
