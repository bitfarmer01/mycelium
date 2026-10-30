import { $, $$, el, num, signed, setVar, clamp01, countUp, hhmmss, RM } from "./dom.js";

/* ---------------------------------------------------------------- ledger feed (FLIP) */
var feedEl = $("#feed");
var FEED_MAX = 40;
var seenLedger = {};
function updateFeedEmpty() {
  $("#feedEmpty").hidden = feedEl.children.length > 0;
  $("#feedCount").textContent = feedEl.children.length + " / " + FEED_MAX;
}
function typeClass(t) {
  t = String(t || "").toLowerCase();
  if (/confirm|promote/.test(t)) return "c-green";
  if (/correct|veto|block/.test(t)) return "c-red";
  if (/prior|gate/.test(t)) return "c-amber";
  if (/decay/.test(t)) return "c-grey";
  if (/lesson/.test(t)) return "c-blue";
  return "c-grey";
}
function feedRow(row) {
  var li = el("li", "feedrow " + typeClass(row.type));
  if (row.id != null) li.dataset.id = String(row.id);
  li.appendChild(el("span", "ftype", row.type == null ? "event" : String(row.type)));
  li.appendChild(el("span", "sep", "·"));
  var target = [row.target_kind, row.target].filter(function (x) { return x != null && x !== ""; }).join(" ");
  li.appendChild(el("span", "ftarget", target || "—"));

  var d = el("span", "fdelta");
  d.appendChild(el("span", "b", num(row.before)));
  d.appendChild(el("span", "arw", "→"));
  var after = el("span", "a", num(row.before));
  d.appendChild(after);
  if (row.delta != null) d.appendChild(el("span", "d", "(" + signed(row.delta) + ")"));
  li.appendChild(d);

  if (typeof row.after === "number" && isFinite(row.after)) {
    var bar = el("span", "fbar"); var fill = el("i");
    setVar(fill, "--f", clamp01(row.before));
    bar.appendChild(fill); li.appendChild(bar);
    requestAnimationFrame(function () { setVar(fill, "--f", clamp01(row.after)); });
    countUp(after, row.before, row.after, function (v) { return num(v); }, 620);
  }

  if (row.cause != null && row.cause !== "") li.appendChild(el("span", "fcause", String(row.cause)));
  var ts = hhmmss(row.ts);
  if (ts) li.appendChild(el("span", "fts", ts));
  li.title = "id " + (row.id == null ? "?" : row.id) + (row.ts ? " · " + row.ts : "");
  return li;
}
function flipCapture() {
  var m = {};
  $$(".feedrow", feedEl).forEach(function (li) { if (li.dataset.id) m[li.dataset.id] = li.getBoundingClientRect(); });
  return m;
}
function flipPlay(prev) {
  if (RM) return;
  $$(".feedrow", feedEl).forEach(function (li) {
    var a = li.dataset.id ? prev[li.dataset.id] : null;
    if (!a) return;
    var b = li.getBoundingClientRect();
    var dy = a.top - b.top;
    if (dy && li.animate) li.animate([{ transform: "translateY(" + dy + "px)" }, { transform: "none" }], { duration: 260, easing: "cubic-bezier(.2,.75,.25,1)" });
  });
}
function feedAdd(row, opts) {
  if (!row || typeof row !== "object") return;
  var id = row.id != null ? String(row.id) : null;
  if (id) { if (seenLedger[id]) return; seenLedger[id] = 1; }
  var prev = flipCapture();
  var li = feedRow(row);
  feedEl.insertBefore(li, feedEl.firstChild);
  while (feedEl.children.length > FEED_MAX) { var last = feedEl.lastElementChild; if (last.dataset.id) delete seenLedger[last.dataset.id]; feedEl.removeChild(last); }
  updateFeedEmpty();
  if (!RM && li.animate) li.animate([{ opacity: 0, transform: "translateY(-6px) scale(.99)" }, { opacity: 1, transform: "none" }], { duration: 240, easing: "ease-out" });
  flipPlay(prev);
}

export { feedAdd, updateFeedEmpty, feedEl };
