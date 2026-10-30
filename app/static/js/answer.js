import { $, $$, el, num, glow } from "./dom.js";
import { state } from "./session.js";
import { setPrior } from "./prior.js";
import { renderChips } from "./corrections.js";
import { speak } from "./audio.js";

/* ------------------------------------------------------------------------ answer -- */
function renderScreen(text) {
  var box = $("#screen"); box.textContent = "";
  String(text == null ? "" : text).split(/(\[\d+\])/g).forEach(function (part) {
    if (!part) return;
    var m = /^\[(\d+)\]$/.exec(part);
    if (!m) { box.appendChild(document.createTextNode(part)); return; }
    var n = m[1];
    var sup = el("sup", "cite", "[" + n + "]");
    sup.tabIndex = 0; sup.setAttribute("role", "button"); sup.setAttribute("aria-label", "citation " + n);
    sup.addEventListener("click", function () { showCitation(n, sup); });
    sup.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); showCitation(n, sup); } });
    box.appendChild(sup);
  });
}
function citationFor(n) {
  var c = (state.ask && state.ask.citations) || [];
  for (var i = 0; i < c.length; i++) if (String(c[i].n) === String(n)) return c[i];
  return null;
}
function showCitation(n, sup) {
  var card = $("#chunkCard");
  $$("sup.cite.open").forEach(function (s) { s.classList.remove("open"); });
  if (!card.hidden && card.dataset.n === String(n)) { card.hidden = true; return; }
  var c = citationFor(n);
  if (!c) { card.hidden = true; return; }
  if (sup) sup.classList.add("open");
  card.dataset.n = String(n); card.textContent = "";
  var head = el("div", "ctitle", "[" + n + "] ");
  if (c.link) { var a = el("a", null, c.title || c.chunk_id || "source"); a.href = c.link; a.target = "_blank"; a.rel = "noopener noreferrer"; head.appendChild(a); }
  else head.appendChild(document.createTextNode(c.title || c.chunk_id || "source"));
  card.appendChild(head);
  if (c.snippet) card.appendChild(el("div", "csnip", String(c.snippet)));
  var meta = el("div", "cmeta");
  if (c.cosine != null) meta.appendChild(el("span", null, "cosine " + num(c.cosine, 3)));
  if (c.fitness != null) meta.appendChild(el("span", null, "fitness " + num(c.fitness, 3)));
  if (c.chunk_id) meta.appendChild(el("span", null, String(c.chunk_id)));
  /* field-level degrade: only scores the payload actually carries are rendered;
     the meta row itself survives (it holds the close control) */
  var close = el("button", "close", "close"); close.type = "button";
  close.addEventListener("click", function () { card.hidden = true; });
  meta.appendChild(close); card.appendChild(meta);
  card.hidden = false; glow(card, "b");
}

function renderAnswer(res) {
  state.ask = res; state.lastSpoken = res.spoken || ""; state.lastAudioUrl = res.audio_url || null;
  var slot = $("#slotBadge"); slot.textContent = "slot " + (res.slot_version == null ? "—" : res.slot_version); slot.className = "pill mono k";
  $("#shapeBadge").textContent = "shape " + (res.shape == null ? "—" : res.shape);
  var cite = $("#citeBadge");
  if (String(res.citations_resolved) === "0/0") { cite.textContent = "citations 0/0 · The wiki does not cover this"; cite.className = "pill mono k"; }
  else { cite.textContent = "citations " + res.citations_resolved + " resolved"; cite.className = "pill mono g"; }
  $("#entropyBadge").textContent = "H " + (res.entropy_bits == null ? "—" : num(res.entropy_bits, 3)) + " bits";
  var ms = $("#msBadge"); ms.textContent = res.ms == null ? "" : res.ms + " ms"; ms.hidden = res.ms == null;
  $("#spoken").textContent = res.spoken == null ? "" : String(res.spoken);
  renderScreen(res.screen);
  $("#chunkCard").hidden = true;
  renderChips(res);
  var why = $("#why"); why.textContent = "";
  why.appendChild(document.createTextNode(String(res.why == null ? "—" : res.why)));
  if (res.taken_reading && res.taken_reading.assumption) why.appendChild(document.createTextNode(" Assumption: " + res.taken_reading.assumption));
  $("#flip").textContent = res.flip_condition == null ? "—" : String(res.flip_condition);
  state.prior = res.prior; setPrior(res.prior, false); $("#priorHint").textContent = "entropy " + num(res.entropy_bits, 3) + " bits over self-reported p";
  var ans = $("#answer"); ans.hidden = false;
  glow(ans, "b"); glow(cite, String(res.citations_resolved) === "0/0" ? "a" : "g");
  speak(res.spoken, res.audio_url);
}

$("#replayBtn").addEventListener("click", function () { speak(state.lastSpoken, state.lastAudioUrl); });

export { renderAnswer };
