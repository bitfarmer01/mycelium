import { $ } from "./dom.js";
import { setStatus } from "./status.js";
import { startPipeline, stopPipeline } from "./pipeline.js";
import { state } from "./session.js";
import { jpost, streamAsk } from "./api.js";
import { renderAnswer } from "./answer.js";
import { scheduleVitals } from "./vitals.js";
import { showAskSkeleton, hideSkeletons } from "./skeletons.js";
import { startTimeline, stageEvent, finishTimeline, failTimeline } from "./timeline.js";

/* ---------------------------------------------------------------------- ask action -- */
var sendBtn = $("#sendBtn"), qInput = $("#q");
/* feature 69: stage events off the /ask/stream pipeline feed the answer
   timeline; `done` pins the total from the answer payload's ms */
function onStageEvent(ev) {
  if (ev.type === "done") finishTimeline(ev.answer && ev.answer.ms != null ? ev.answer.ms : ev.ms);
  else if (ev.type === "error") failTimeline();
  else stageEvent(ev);
}
async function ask() {
  var text = qInput.value.trim();
  if (!text) { setStatus("type a question, or hold to talk", "err"); qInput.focus(); return; }
  /* synchronous DOM first (feature 68): skeleton + timeline on screen before
     any await, so the 100 ms budget survives even a stalled network */
  sendBtn.disabled = true; setStatus("asking… (about 25 s; the pipeline is live)", "busy");
  startPipeline(text);
  startTimeline();
  showAskSkeleton();
  try {
    var res = await streamAsk(text, state.sessionId, onStageEvent);
    stopPipeline(); hideSkeletons();
    renderAnswer(res);
    setStatus("answered" + (res.ms == null ? "" : " in " + res.ms + " ms"), "ok");
    scheduleVitals();
  } catch (streamErr) {
    /* demo resilience: the non-streaming path still answers if the stream
       dies mid-flight; the timeline degrades to a total-only row */
    try {
      var res2 = await jpost("/ask", { text: text, session_id: state.sessionId });
      stopPipeline(); hideSkeletons();
      renderAnswer(res2);
      finishTimeline(res2.ms == null ? null : res2.ms);  /* no stage events — total only, stages stay at — */
      setStatus("answered (non-streaming)" + (res2.ms == null ? "" : " in " + res2.ms + " ms"), "ok");
      scheduleVitals();
    } catch (e) {
      stopPipeline(); hideSkeletons(); failTimeline();
      setStatus("ask failed: " + e.message, "err");
    }
  } finally { sendBtn.disabled = false; }
}

sendBtn.addEventListener("click", ask);
qInput.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); ask(); } });

export { ask };
