import { $, el } from "./dom.js";

/* --------------------------------------------------------------------- skeletons --
   feature 68: skeleton loaders for ask / correct / reflect. Every builder is
   synchronous DOM insertion called from the click handler BEFORE the first
   await — the skeleton is on screen in well under 100 ms no matter how slow
   the network or model is, so async actions never show dead air. The shimmer
   animation dies under the global prefers-reduced-motion rule (bars render
   as static blocks). */

function bar(cls, w) {
  var b = el("span", "skelbar" + (cls ? " " + cls : ""));
  if (w) b.style.width = w;
  return b;
}
function row(n, cls, widths) {
  var r = el("div", "skelrow");
  for (var i = 0; i < n; i++) r.appendChild(bar(cls, widths ? widths[i % widths.length] : null));
  return r;
}
function chips(n) {
  var r = el("div", "skelrow");
  for (var i = 0; i < n; i++) {
    var c = el("span", "skelchip");
    c.appendChild(bar(null, "42%"));
    c.appendChild(bar(null, "88%"));
    c.appendChild(bar(null, "34%"));
    r.appendChild(c);
  }
  return r;
}
function mount(node) {
  var box = $("#skeleton");
  box.textContent = "";
  box.appendChild(node);
  box.hidden = false;
}

/* ask: metabar pills → spoken line → screen lines → chips → why lines */
function askSkeleton() {
  var w = el("div", "skelwrap");
  w.appendChild(row(4, "skelpill"));
  w.appendChild(row(1, null, ["92%"]));
  w.appendChild(row(2, null, ["100%", "76%"]));
  w.appendChild(chips(3));
  w.appendChild(row(2, null, ["64%", "48%"]));
  mount(w);
}
/* correct: the chips row is what the preview will rewrite */
function correctSkeleton() {
  var w = el("div", "skelwrap");
  w.appendChild(chips(3));
  mount(w);
}
/* reflect: sentence cards inside the learn column; renderReflect clears the
   same container on success, error paths call clearReflectSkeleton */
function reflectSkeleton() {
  var box = $("#sentences");
  box.textContent = "";
  for (var i = 0; i < 3; i++) {
    var s = el("div", "sentence skelsentence");
    s.appendChild(row(1, null, [i % 2 ? "71%" : "96%"]));
    s.appendChild(row(2, "skelrid", ["22%", "30%"]));
    box.appendChild(s);
  }
}
function clearReflectSkeleton() {
  var box = $("#sentences");
  if (!box.querySelector(".skelsentence")) return;
  box.textContent = "";
  box.appendChild(el("p", "empty", "Ask for a reflection to see today's lessons."));
}
function hideSkeletons() { $("#skeleton").hidden = true; }

export { askSkeleton as showAskSkeleton, correctSkeleton as showCorrectSkeleton, reflectSkeleton as showReflectSkeleton, clearReflectSkeleton, hideSkeletons };
