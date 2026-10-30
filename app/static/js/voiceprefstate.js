/* ------------------------------------------------------------- voice prefs state -- */
/* Feature 57: in-page store for the per-user voice preference. Deliberately
   import-free so audio.js can read live values without an api.js cycle — the
   loader/saver (voiceprefs.js) writes here, the playback path (audio.js) reads. */

var VOICE_PREFS_EVENT = "mycelium:voiceprefs";
var USER_KEY = "mycelium.user";

var DEFAULT_VOLUME = 1.0;
var DEFAULT_SPEED = 1.0;
var VOLUME_RANGE = [0.0, 1.0]; /* mirrors core.prefs.VOLUME_RANGE */
var SPEED_RANGE = [0.5, 2.0]; /* mirrors core.prefs.SPEED_RANGE */

var prefs = { volume: DEFAULT_VOLUME, speed: DEFAULT_SPEED };

function clamp(v, bounds) {
  v = Number(v);
  if (!isFinite(v)) return bounds[0];
  return Math.max(bounds[0], Math.min(bounds[1], v));
}
function clampVolume(v) { return clamp(v, VOLUME_RANGE); }
function clampSpeed(s) { return clamp(s, SPEED_RANGE); }

function getVoicePrefs() {
  return { volume: prefs.volume, speed: prefs.speed };
}
function setVoicePrefs(volume, speed) {
  prefs.volume = clampVolume(volume);
  prefs.speed = clampSpeed(speed);
  return getVoicePrefs();
}

export { VOICE_PREFS_EVENT, USER_KEY, DEFAULT_VOLUME, DEFAULT_SPEED, VOLUME_RANGE, SPEED_RANGE,
         clampVolume, clampSpeed, getVoicePrefs, setVoicePrefs };
