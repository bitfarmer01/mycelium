import { $, el, glow } from "./dom.js";
import { jpost } from "./api.js";
import { setStatus } from "./status.js";
import { scheduleVitals } from "./vitals.js";
import { speak } from "./audio.js";

/* ------------------------------------------------------------------------- reflect -- */
function renderReflect(res) {
  var box = $("#sentences"); box.textContent = "";
  var list = res.sentences || [];
  $("#reflectMeta").textContent = list.length + " sentences · rows read " + (res.rows_read == null ? "—" : res.rows_read) + " · dropped " + (res.dropped == null ? "0" : res.dropped) + (res.llm ? " · llm" : "");
  list.forEach(function (s) {
    var node = el("div", "sentence");
    node.appendChild(document.createTextNode(String(s.text == null ? "" : s.text)));
    var row = el("div", "srow");
    (s.row_ids || []).forEach(function (id) { row.appendChild(el("span", "ridchip", String(id))); });
    var play = el("button", "btn tiny", "play"); play.type = "button";
    play.addEventListener("click", function () { speak(s.text, null); });
    row.appendChild(play); node.appendChild(row); box.appendChild(node);
  });
  if (!list.length) box.appendChild(el("p", "empty", "nothing yet today"));
  glow($("#reflectBox"), "b");
  if (res.audio_url || res.spoken) speak(res.spoken || list.map(function (s) { return s.text; }).join(" "), res.audio_url);
}
function renderLesson(row) {
  var box = $("#sentences");
  var empty = box.querySelector(".empty"); if (empty) box.textContent = "";
  var node = el("div", "sentence");
  node.appendChild(document.createTextNode(String(row.cause || "lesson written")));
  var srow = el("div", "srow");
  srow.appendChild(el("span", "ridchip", String(row.id || "")));
  if (row.target) srow.appendChild(el("span", "ridchip", String(row.target)));
  node.appendChild(srow);
  box.insertBefore(node, box.firstChild); glow(node, "b");
}
async function reflect() {
  setStatus("reflecting… (may take ~30 s)", "busy");
  try {
    var res = await jpost("/reflect", {});
    renderReflect(res);
    var n = (res.sentences || []).length;
    setStatus(n + " sentence" + (n === 1 ? "" : "s") + " · rows read " + (res.rows_read == null ? "—" : res.rows_read) + " · dropped " + (res.dropped == null ? "0" : res.dropped), "ok");
    scheduleVitals();
  } catch (e) { setStatus("reflect failed: " + e.message, "err"); }
}

$("#reflectBtn").addEventListener("click", reflect);

export { renderReflect, renderLesson };
