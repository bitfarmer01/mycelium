/* ----------------------------------------------------------------- silent mode -- */
/* Feature 60: silent-demo toggle. When on, audio.speak() skips every TTS path —
   zero ElevenLabs credit burn — while captions keep rendering the spoken text.
   Preference persists across sessions. */

var KEY = "mycelium.silent";
var SILENT_CHANGE_EVENT = "mycelium:silentchange";

function isSilent() {
  try { return window.localStorage.getItem(KEY) === "1"; }
  catch (e) { return false; }
}
function setSilent(on) {
  try {
    if (on) window.localStorage.setItem(KEY, "1");
    else window.localStorage.removeItem(KEY);
  } catch (e) { /* storage unavailable (private mode): session-only default */ }
}

export { KEY, isSilent, setSilent, SILENT_CHANGE_EVENT };

/* Wire the checkbox when the console DOM is present. Node (tests) has no document. */
if (typeof document !== "undefined") {
  var box = document.getElementById("silentMode");
  if (box) {
    box.checked = isSilent();
    box.addEventListener("change", function () {
      setSilent(box.checked);
      document.dispatchEvent(new CustomEvent(SILENT_CHANGE_EVENT, { detail: { on: box.checked } }));
    });
  }
}
