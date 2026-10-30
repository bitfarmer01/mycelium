import { $, el } from "./dom.js";

/* ------------------------------------------------------------------ ask + pipeline -- */
function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
var pipeTimer = null, pipeAdv = null, pipeSteps = [], pipeT0 = 0;
function startPipeline(text) {
  var p = $("#pipeline"); p.hidden = false;
  $("#pipeQuestion").textContent = text;
  $("#pipeTitle").textContent = "working — reading, retrieval, synthesis, citation resolve";
  var flow = $("#pipeFlow"); flow.textContent = "";
  pipeSteps = ["readings", "retrieval", "synthesis", "resolve"].map(function (s, i) {
    var n = el("span", "pipestep" + (i === 0 ? " live" : ""));
    n.appendChild(el("span", "sd")); n.appendChild(document.createTextNode(s));
    flow.appendChild(n); return n;
  });
  pipeT0 = now(); pipeIdx = 0;
  var tick = function () { $("#pipeElapsed").textContent = Math.floor((now() - pipeT0) / 1000) + " s"; };
  tick(); clearInterval(pipeTimer); pipeTimer = setInterval(tick, 250);
  clearInterval(pipeAdv);
  pipeAdv = setInterval(function () {
    if (pipeIdx < pipeSteps.length - 1) { pipeSteps[pipeIdx].className = "pipestep done"; pipeIdx++; pipeSteps[pipeIdx].className = "pipestep live"; }
  }, 2600);
}
var pipeIdx = 0;
function stopPipeline() { clearInterval(pipeTimer); clearInterval(pipeAdv); $("#pipeline").hidden = true; }

export { startPipeline, stopPipeline };
