import { $ } from "./dom.js";
import { jget, jput } from "./api.js";
import { setStatus } from "./status.js";
import { getVoicePrefs, setVoicePrefs, USER_KEY, VOICE_PREFS_EVENT, DEFAULT_VOLUME, DEFAULT_SPEED } from "./voiceprefstate.js";

/* ----------------------------------------------------------------- voice prefs -- */
/* Feature 57: per-user volume/speed, stored in SurrealDB via /voice/prefs and
   loaded on every session boot so the preference follows the user, not the
   browser tab. Sliders apply immediately to speech output and persist on change. */

var volPref, speedPref;

/* Stable per-user id in localStorage — the SurrealDB row key. New users get a
   generated id on first use; "next session load" then finds the same user. */
function userId() {
  try {
    var u = window.localStorage.getItem(USER_KEY);
    if (u) return u;
    u = "user-" + Math.random().toString(36).slice(2, 10);
    window.localStorage.setItem(USER_KEY, u);
    return u;
  } catch (e) { return "default"; }
}

function applyToSliders() {
  var p = getVoicePrefs();
  if (volPref) volPref.value = String(p.volume);
  if (speedPref) speedPref.value = String(p.speed);
}

async function savePref(patch) {
  try {
    var saved = await jput("/voice/prefs", { user: userId(), volume: patch.volume, speed: patch.speed });
    setVoicePrefs(saved.volume, saved.speed);
    applyToSliders();
    document.dispatchEvent(new CustomEvent(VOICE_PREFS_EVENT, { detail: getVoicePrefs() }));
    setStatus("voice preference saved", "ok");
  } catch (e) {
    /* prefs are a comfort feature: never block speaking on a save failure */
    setStatus("voice preference not saved: " + e.message, "err");
  }
}

function wireSliders() {
  volPref = $("#volPref"); speedPref = $("#speedPref");
  if (!volPref || !speedPref) return false;
  volPref.addEventListener("input", function () { setVoicePrefs(volPref.value, getVoicePrefs().speed); savePref({ volume: Number(volPref.value) }); });
  speedPref.addEventListener("input", function () { setVoicePrefs(getVoicePrefs().volume, speedPref.value); savePref({ speed: Number(speedPref.value) }); });
  return true;
}

async function initVoicePrefs() {
  if (!wireSliders()) return;
  try {
    var p = await jget("/voice/prefs?user=" + encodeURIComponent(userId()));
    setVoicePrefs(p.volume == null ? DEFAULT_VOLUME : p.volume, p.speed == null ? DEFAULT_SPEED : p.speed);
    applyToSliders();
    document.dispatchEvent(new CustomEvent(VOICE_PREFS_EVENT, { detail: getVoicePrefs() }));
  } catch (e) { /* defaults already applied; a demo speaks fine on defaults */ }
}

export { initVoicePrefs, userId, savePref };
