import { $ } from "./dom.js";

/* --------------------------------------------------------------------- status line -- */
var statusEl = $("#status");
function setStatus(msg, kind) {
  statusEl.className = "status" + (kind ? " " + kind : "");
  statusEl.textContent = msg || "";
}

export { setStatus };
