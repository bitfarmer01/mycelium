/* --------------------------------------------------------------------- theme -- */
/* Feature 67: theme switch flips the :root token set without a reload.
   Cycles dark → light → dim (reduced glare), persists per browser, and updates
   the color-scheme hint so form controls follow. Node (tests) has no document. */

var KEY = "mycelium.theme";
var THEMES = ["dark", "light", "dim"];
var LABELS = { dark: "Theme: dark", light: "Theme: light", dim: "Theme: reduced glare" };
var CHANGE_EVENT = "mycelium:themechange";

function isTheme(t) { return THEMES.indexOf(t) !== -1; }

function currentTheme() {
  try {
    var t = window.localStorage.getItem(KEY);
    if (isTheme(t)) return t;
  } catch (e) { /* storage unavailable: fall through to default */ }
  return "dark";
}

/* apply the token set: data-theme on <html> selects the CSS token block */
function applyTheme(t) {
  if (typeof document === "undefined") return t;
  document.documentElement.dataset.theme = t;
  document.documentElement.style.colorScheme = t === "light" ? "light" : "dark";
  var btn = document.getElementById("themeBtn");
  if (btn) {
    btn.textContent = LABELS[t] || LABELS.dark;
    btn.setAttribute("aria-label", LABELS[t] + ". Activate to switch theme");
  }
  return t;
}

function setTheme(t) {
  if (!isTheme(t)) t = "dark";
  try { window.localStorage.setItem(KEY, t); } catch (e) { /* session-only theme */ }
  applyTheme(t);
  if (typeof document !== "undefined") {
    document.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: { theme: t } }));
  }
  return t;
}

function cycleTheme() { return setTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length]); }

export { KEY, THEMES, LABELS, CHANGE_EVENT, isTheme, currentTheme, applyTheme, setTheme, cycleTheme };

/* Wire the header toggle when the console DOM is present. */
if (typeof document !== "undefined") {
  applyTheme(currentTheme());
  var btn = document.getElementById("themeBtn");
  if (btn) btn.addEventListener("click", cycleTheme);
}
