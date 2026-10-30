/* ---------------------------------------------------------------- captions mode -- */
/* Feature 56: caption-always toggle. When on (the default), the transcript
   renders beside every spoken answer. When off, the caption block is hidden.
   Preference persists across sessions; absence of the key means ON so silent
   mode (feature 60) always has captions unless they were explicitly disabled. */

var KEY = "mycelium.captions";
var CAPTIONS_CHANGE_EVENT = "mycelium:captionschange";

function isCaptions() {
  try { return window.localStorage.getItem(KEY) !== "0"; }
  catch (e) { return true; }
}
function setCaptions(on) {
  try {
    if (on) window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, "0");
  } catch (e) { /* storage unavailable (private mode): session-only default */ }
}

export { KEY, isCaptions, setCaptions, CAPTIONS_CHANGE_EVENT };

/* Wire the checkbox when the console DOM is present. Node (tests) has no document. */
if (typeof document !== "undefined") {
  var box = document.getElementById("captionsMode");
  if (box) {
    box.checked = isCaptions();
    box.addEventListener("change", function () {
      setCaptions(box.checked);
      document.dispatchEvent(new CustomEvent(CAPTIONS_CHANGE_EVENT, { detail: { on: box.checked } }));
    });
  }
}
