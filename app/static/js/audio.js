import { $ } from "./dom.js";
import { MOCK } from "./mode.js";
import { isSilent, SILENT_CHANGE_EVENT } from "./silent.js";

/* --------------------------------------------------------------------------- audio -- */
var audioStateEl = $("#audioState");
var currentAudio = null;
function setAudio(text, cls) {
  audioStateEl.textContent = text;
  audioStateEl.className = "pill mono" + (cls ? " " + cls : "");
}
function eqMark() { return '<span class="eq" aria-hidden="true"><i></i><i></i><i></i><i></i></span>'; }
function browserSpeak(text) {
  try {
    if (!window.speechSynthesis || !text) { setAudio("audio idle", ""); return; }
    speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(String(text));
    u.onend = function () { setAudio("audio idle", ""); };
    speechSynthesis.speak(u);
    setAudio("browser voice (speechSynthesis)", "a");
  } catch (e) { setAudio("browser voice (speechSynthesis)", "a"); }
}
function stopAudio() {
  if (currentAudio) { try { currentAudio.pause(); } catch (e) {} currentAudio = null; }
  try { if (window.speechSynthesis) speechSynthesis.cancel(); } catch (e) {}
  setAudio("audio idle", "");
}
/* play audio_url; on ANY error, speak the same text with speechSynthesis.
   Silent mode (feature 60) returns before any Audio construction or /tts fetch:
   zero ElevenLabs credit burn — captions (#spoken) still show the text. */
function speak(text, audioUrl) {
  stopAudio();
  if (isSilent()) { setAudio("silent — captions only", "b"); return; }
  if (MOCK) { setAudio("playing", "g"); setTimeout(function () { setAudio("audio idle", ""); }, 1200); return; }
  var src = audioUrl || (text ? "/tts?text=" + encodeURIComponent(text) : null);
  if (!src) { setAudio("audio idle", ""); return; }
  var a;
  try { a = new Audio(src); } catch (e) { browserSpeak(text); return; }
  currentAudio = a;
  setAudio("playing", "g");
  a.onended = function () { setAudio("audio idle", ""); };
  a.onerror = function () { browserSpeak(text); };
  try {
    var p = a.play();
    if (p && typeof p.catch === "function") p.catch(function () { browserSpeak(text); });
  } catch (e) { browserSpeak(text); }
}

export { speak, browserSpeak, stopAudio };

/* Toggling silent mid-playback cuts the current audio immediately (feature 60). */
if (typeof document !== "undefined") {
  document.addEventListener(SILENT_CHANGE_EVENT, function (ev) {
    if (ev && ev.detail && ev.detail.on) stopAudio();
  });
}
