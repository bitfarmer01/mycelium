import { $ } from "./dom.js";
import { setStatus } from "./status.js";
import { stopAudio } from "./audio.js";

/* ------------------------------------------------------------------------- session -- */
var state = {
  sessionId: null, ask: null, prior: null, lastSpoken: "", lastAudioUrl: null,
  vitals: null, config: null, lastCounts: null, vitalsLoaded: false,
  candidates: {}, wsTries: 0, prevSlot: null
};
function newSessionId() {
  try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
  return "sess-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}
function renderSession() {
  var b = $("#sessionBadge");
  b.textContent = "session " + String(state.sessionId).slice(0, 8);
  b.title = "session id: " + state.sessionId;
}

function newSession() {
  state.sessionId = newSessionId(); renderSession();
  state.ask = null; state.prior = null;
  $("#answer").hidden = true; $("#chunkCard").hidden = true;
  setStatus("new session — corrections will not carry to the next person", "ok");
  stopAudio();
}

$("#newSessionBtn").addEventListener("click", newSession);

export { state, newSessionId, renderSession, newSession };
