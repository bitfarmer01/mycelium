import { $, glow } from "./dom.js";
import { MOCK } from "./mode.js";
import { jpostForm } from "./api.js";
import { setStatus } from "./status.js";

var qInput = $("#q"); /* shared with ask.js — transcripts land in the ask input */

/* ---------------------------------------------------------------------- hold to talk -- */
var talkBtn = $("#talkBtn");
var rec = null, recChunks = [], recStream = null, recStart = 0, recActive = false;
function pickMime() {
  var opts = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
  for (var i = 0; i < opts.length; i++) if (MediaRecorder.isTypeSupported(opts[i])) return opts[i];
  return "";
}
function applyTranscript(text) {
  text = String(text == null ? "" : text).trim();
  qInput.value = text;
  setStatus(text ? "transcript ready — press Send" : "no speech detected", text ? "ok" : "err");
  if (text) { qInput.focus(); glow(qInput, "b"); }
}
function talkDown(ev) {
  if (ev && ev.preventDefault) ev.preventDefault();
  if (recActive) return;
  if (MOCK) { recActive = true; talkBtn.classList.add("rec"); talkBtn.textContent = "listening…"; talkBtn.setAttribute("aria-pressed", "true"); setStatus("recording (mock) — release to transcribe", "busy"); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) { setStatus("recording not supported here — type instead", "err"); return; }
  recActive = true; talkBtn.classList.add("rec"); talkBtn.textContent = "listening…"; talkBtn.setAttribute("aria-pressed", "true");
  navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
    recStream = stream; recChunks = [];
    var mime = pickMime();
    try { rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream); } catch (e) { rec = new MediaRecorder(stream); }
    rec.ondataavailable = function (e) { if (e.data && e.data.size) recChunks.push(e.data); };
    rec.onstop = function () {
      var type = rec.mimeType || "audio/webm";
      if (recStream) recStream.getTracks().forEach(function (t) { t.stop(); });
      recStream = null;
      var blob = new Blob(recChunks, { type: type }); recChunks = [];
      if (blob.size < 1200 || Date.now() - recStart < 400) { setStatus("hold the button while you speak", "err"); return; }
      stt(blob);
    };
    recStart = Date.now(); rec.start(); setStatus("recording — release to transcribe", "busy");
  }).catch(function () { talkUp(); setStatus("microphone permission denied", "err"); });
}
function talkUp() {
  talkBtn.classList.remove("rec"); talkBtn.textContent = "Hold to talk"; talkBtn.setAttribute("aria-pressed", "false");
  if (MOCK && recActive) { recActive = false; applyTranscript("how do we run a discovery call?"); return; }
  if (rec && rec.state === "recording") { try { rec.stop(); } catch (e) {} }
  recActive = false;
}
async function stt(blob) {
  setStatus("transcribing…", "busy");
  try {
    var fd = new FormData(); fd.append("audio", blob, "audio.webm");
    var data = await jpostForm("/stt", fd);
    applyTranscript(data && data.text);
  } catch (e) { setStatus("transcription failed: " + e.message, "err"); }
}

talkBtn.addEventListener("pointerdown", talkDown);
talkBtn.addEventListener("pointerup", function (ev) { ev.preventDefault(); talkUp(); });
talkBtn.addEventListener("pointercancel", talkUp);
talkBtn.addEventListener("pointerleave", function () { if (recActive) talkUp(); });
talkBtn.addEventListener("contextmenu", function (e) { e.preventDefault(); });
talkBtn.addEventListener("keydown", function (ev) { if ((ev.key === " " || ev.key === "Enter") && !recActive) { ev.preventDefault(); talkDown(ev); } });
talkBtn.addEventListener("keyup", function (ev) { if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); talkUp(); } });

export { applyTranscript };
