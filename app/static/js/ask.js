import { $ } from "./dom.js";
import { setStatus } from "./status.js";
import { startPipeline, stopPipeline } from "./pipeline.js";
import { state } from "./session.js";
import { jpost } from "./api.js";
import { renderAnswer } from "./answer.js";
import { scheduleVitals } from "./vitals.js";

/* ---------------------------------------------------------------------- ask action -- */
var sendBtn = $("#sendBtn"), qInput = $("#q");
async function ask() {
  var text = qInput.value.trim();
  if (!text) { setStatus("type a question, or hold to talk", "err"); qInput.focus(); return; }
  sendBtn.disabled = true; setStatus("asking… (about 25 s; the pipeline is live)", "busy");
  startPipeline(text);
  try {
    var res = await jpost("/ask", { text: text, session_id: state.sessionId });
    stopPipeline();
    renderAnswer(res);
    setStatus("answered" + (res.ms == null ? "" : " in " + res.ms + " ms"), "ok");
    scheduleVitals();
  } catch (e) {
    stopPipeline(); setStatus("ask failed: " + e.message, "err");
  } finally { sendBtn.disabled = false; }
}

sendBtn.addEventListener("click", ask);
qInput.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); ask(); } });

export { ask };
