/* ------------------------------------------------------------------------- helpers -- */
var $ = function (s, r) { return (r || document).querySelector(s); };
var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = String(text);
  return n;
}
function clone(o) { try { return JSON.parse(JSON.stringify(o)); } catch (e) { return o; } }
function num(v, dp) {
  if (v == null || v === "") return "—";
  var n = Number(v);
  if (!isFinite(n)) return String(v);
  if (dp == null) return (Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(3)).replace(/0+$/, "").replace(/\.$/, "");
  return n.toFixed(dp);
}
function fmtInt(v) { if (v == null || v === "") return "—"; var n = Number(v); return isFinite(n) ? String(Math.round(n)) : String(v); }
function signed(v, dp) {
  if (v == null || v === "") return "—";
  var n = Number(v); if (!isFinite(n)) return String(v);
  return (n >= 0 ? "+" : "") + num(n, dp == null ? 3 : dp);
}
function clamp01(v) { v = Number(v); return isFinite(v) ? Math.max(0, Math.min(1, v)) : 0; }
function meanOf(m) {
  if (Array.isArray(m)) { var xs = m.map(Number).filter(isFinite); return xs.length ? xs.reduce(function (a, b) { return a + b; }, 0) / xs.length : null; }
  var n = Number(m); return isFinite(n) ? n : null;
}
function fmtBytes(b) {
  if (b == null) return "—";
  var n = Number(b);
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}
function hhmmss(ts) {
  if (!ts) return "";
  var m = String(ts).match(/T(\d\d:\d\d:\d\d)/);
  return m ? m[1] : "";
}
var RM = false;
try { RM = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { RM = false; }

/* count a number up from before → after with requestAnimationFrame (instant if reduced) */
function countUp(node, from, to, fmt, ms) {
  if (!node) return;
  fmt = fmt || num;
  var f = Number(from), t = Number(to);
  if (!isFinite(t)) { node.textContent = fmt(to); return; }
  if (RM || !isFinite(f) || !ms || ms <= 0 || f === t) { node.textContent = fmt(t); return; }
  var t0 = (window.performance && performance.now) ? performance.now() : Date.now();
  (function step() {
    var now = (window.performance && performance.now) ? performance.now() : Date.now();
    var k = Math.min(1, (now - t0) / ms);
    var e = 1 - Math.pow(1 - k, 3);
    node.textContent = fmt(f + (t - f) * e);
    if (k < 1) requestAnimationFrame(step);
  })();
}
/* soft glow on the element that just changed; removed after the animation */
function glow(node, color) {
  if (!node) return;
  var cls = "glow-" + color;
  node.classList.remove("glow-g", "glow-r", "glow-a", "glow-b");
  void node.offsetWidth;
  node.classList.add(cls);
  clearTimeout(node.__glowT);
  node.__glowT = setTimeout(function () { node.classList.remove(cls); }, 1600);
}
/* set a CSS custom property (used for prior bars + fitness bars; transitioned in CSS) */
function setVar(node, name, v) { if (node) node.style.setProperty(name, String(v)); }

export { $, $$, el, clone, num, fmtInt, signed, clamp01, meanOf, fmtBytes, hhmmss, RM, countUp, glow, setVar };
