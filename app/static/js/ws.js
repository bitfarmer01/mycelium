import { $, clone } from "./dom.js";
import { state } from "./session.js";
import { MOCK, AUDIT } from "./mode.js";
import { FX } from "./fixtures.js";
import { handleEvent } from "./events.js";

/* ---------------------------------------------------------------------- websocket -- */
var ws = null, wsTimer = null;
function setWs(on) {
  $("#wsDot").classList.toggle("on", !!on);
  $("#wsText").textContent = on ? "live" : "retrying";
  $("#wsBadge").title = on ? "event stream connected" : "event stream disconnected (retrying)";
}
function connectWS() {
  if (MOCK) { mockWs(); return; }
  var proto = location.protocol === "https:" ? "wss:" : "ws:";
  try { ws = new WebSocket(proto + "//" + location.host + "/events"); } catch (e) { setWs(false); scheduleReconnect(); return; }
  ws.onopen = function () { state.wsTries = 0; setWs(true); };
  ws.onmessage = function (ev) { var m; try { m = JSON.parse(ev.data); } catch (e) { return; } handleEvent(m); };
  ws.onclose = function () { setWs(false); scheduleReconnect(); };
  ws.onerror = function () { try { ws.close(); } catch (e) {} };
}
function scheduleReconnect() {
  var n = Math.min(state.wsTries++, 6);
  var d = Math.min(30000, 600 * Math.pow(2, n)) + Math.floor(Math.random() * 300);
  clearTimeout(wsTimer); wsTimer = setTimeout(connectWS, d);
}
function mockWs() {
  setWs(true);
  if (AUDIT) return;                 // the audit drives the WS frames itself
  var li = 0, ci = 0;
  var t1 = setInterval(function () {
    if (li >= FX.ledger.length) { clearInterval(t1); return; }
    handleEvent({ table: "ledger", action: "CREATE", row: clone(FX.ledger[li++]) });
  }, 1500);
  var t2 = setInterval(function () {
    if (ci >= FX.candidates.length) { clearInterval(t2); return; }
    handleEvent({ table: "candidate", action: "CREATE", row: clone(FX.candidates[ci++]) });
  }, 1900);
  /* feature 64: replay the live metrics push so the panel moves in mock mode */
  var mi = 0;
  var t3 = setInterval(function () {
    if (mi >= FX.metricsFrames.length) { clearInterval(t3); return; }
    handleEvent({ table: "vitals", action: "METRICS", row: { metrics: clone(FX.metricsFrames[mi++]) } });
  }, 2600);
}

/* central stop for the beforeunload path in main.js (ws + wsTimer are private here) */
function teardown() { clearTimeout(wsTimer); try { if (ws) ws.close(); } catch (e) {} }
export { connectWS, setWs, teardown };
