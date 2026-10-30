import { $, glow } from "./dom.js";
import { jpost } from "./api.js";
import { setStatus } from "./status.js";
import { feedAdd } from "./feed.js";
import { scheduleVitals } from "./vitals.js";

/* ---------------------------------------------------------------------- stale tick -- */
function renderTickResult(res) {
  var n = res && res.decayed != null ? res.decayed : "?";
  feedAdd({ id: "ledger:decay:" + Date.now().toString(36), type: "decay", target: "corpus", target_kind: "fitness", before: 1, after: 0.95, delta: -0.05, cause: "stale tick · 5% toward prior · " + n + " chunks", ts: new Date().toISOString() });
  glow($("#feed").firstChild, "a");
  setStatus("tick · decayed " + n, "ok");
}
async function tick() {
  setStatus("ticking…", "busy");
  try { renderTickResult(await jpost("/tick", {})); scheduleVitals(); }
  catch (e) { setStatus("tick failed: " + e.message, "err"); }
}

$("#tickBtn").addEventListener("click", tick);

export { renderTickResult };
