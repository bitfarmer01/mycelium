/* =====================================================================================
   MYCELIUM · EVENT → FEEDBACK MAP
   Every action and every learning event must move a NAMED value from before → after, in a
   region the audit can watch. [brackets] name the DOM region the audit binds a
   MutationObserver to. Build exactly this map.

   LEDGER ROWS  (WS /events, table:"ledger", deduped by row.id)
     confirm   [feed]        green row, Δ before→after, chunk fitness bar grows + green glow
     correct   [feed]        red row naming the correction; the prior panel travels (below)
     prior     [prior]       amber glow on the prior panel; amber row steps one bar
     decay     [feed]        grey row, fitness shrinks toward prior; corpus greyed
     veto      [feed]        red row naming the matched pattern; BLOCK card if improve is open
     gate      [candidates]  amber PASS/VETO decision drawn on the baseline band
     promote   [slotBadge]   slot chip bumps version, green glow, green feed row
     lesson    [sentences]   lesson sentence appears carrying its row_ids

   CANDIDATE ROWS / status + verdict   [candidates]
     vetoed_prelint           → red BLOCK card naming the matched pattern (no sandbox ran)
     scored + PASS            → green PASS card + baseline band + candidate dot
     scored + VETO            → red VETO card + band, dot below the baseline
     clean_awaiting_baseline  → amber PENDING card, "awaiting baseline"
     error                    → red ERROR card carrying the message
     verdict PASS             → green tag + green glow
     verdict VETO             → red tag + red glow

   API RESPONSES
     POST /ask  start        [pipeline]    pulsing pipeline, echoed question, honest elapsed s
     POST /ask  answered     [answer]      answer appears; shape / citations / H / slot / ms
     /ask citations n/n      [citeBadge]   green "n/n resolved"; blue [N] sups → chunk card
     /ask citations 0/0      [citeBadge]   grey "0/0 · The wiki does not cover this"
     POST /correct           [prior]       prior_before → prior_after travel + count-up
     POST /confirm           [feed]        green confirm row, taken posterior → 1
     POST /tick              [feed]        grey decay row "decayed N"
     POST /reflect           [sentences]   sentences + meta (rows_read / dropped / llm)
     POST /improve           [candidates]  candidate cards + band, or the pending baseline text
     GET  /vitals            [vitalsBody]  counts count up; slot, baseline, runner
     GET  /config            [vitalsBody]  constants strip (decay, prior rates, ratio)
     POST /stt               [status]      transcript lands in the input, status updates
     GET  /tts               [audioState]  "playing" with a moving equaliser
     /tts fallback           [audioState]  amber "browser voice (speechSynthesis)" + [voiceFallback] badge visible

   UPLOAD   [uploadFiles] [uploadRejected] [uploadError]
     accepted        → per-file chip: kind + size → indexing pulse → chunk count-up
     ok              → corpus counter in Vitals ticks up by res.chunks
     rejected file   → chip stays visible with the server's reason
     pre-check fail  → client-side chip with reason (count / type / size), never sent
     413             → red banner with the server detail
     429             → amber banner "another upload is indexing"

   CONNECTION / ERRORS
     ws down   [wsDot]  dot grey, badge "retrying"; exponential backoff reconnect
     ws up     [wsDot]  dot green + beacon
     any call fails     [status]  red status line naming the endpoint
   ===================================================================================== */


import { $ } from "./dom.js";
import { AUDIT } from "./mode.js";
import { newSession } from "./session.js";
import { updateFeedEmpty } from "./feed.js";
import { loadVitals, loadConfig } from "./vitals.js";
import { connectWS, teardown } from "./ws.js";
import { runAudit } from "./audit.js";
import "./theme.js"; /* side-effect module: applies the persisted theme, wires the toggle */
import "./ask.js"; /* side-effect module: wires the #sendBtn / Enter-key ask listeners */

newSession();
updateFeedEmpty();
loadVitals(false);
loadConfig();
connectWS();
var vitalsTimer = setInterval(function () { loadVitals(true); }, 20000);
window.addEventListener("beforeunload", function () {
  clearInterval(vitalsTimer);
  teardown();
});
if (AUDIT) { $("#audit").hidden = false; setTimeout(runAudit, 80); }
