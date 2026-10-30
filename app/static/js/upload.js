import { $, el, fmtBytes, fmtInt, countUp, glow } from "./dom.js";
import { jpostForm, pollUpload } from "./api.js";
import { setStatus } from "./status.js";
import { bumpCorpus, scheduleVitals } from "./vitals.js";

/* ------------------------------------------------------------------------ upload -- */
var SUPPORTED = [".md", ".markdown", ".txt", ".html", ".htm", ".pdf", ".csv", ".tsv", ".xlsx"];
var MAX_FILES = 10, MAX_FILE = 20 * 1024 * 1024, MAX_TOTAL = 50 * 1024 * 1024, MAX_CHUNKS = 800, MAX_DIR = 500 * 1024 * 1024;
var uploadChips = {}, uploadTotalChunks = 0;

function makeChip(name, kind, size) {
  var chip = el("div", "uchip");
  chip.appendChild(el("span", "ukind", kind || "?"));
  chip.appendChild(el("span", "uname", name));
  chip.appendChild(el("span", "usize mono", size == null ? "" : fmtBytes(size)));
  chip.appendChild(el("span", "ustate", "queued"));
  return chip;
}
function addChip(name, kind, size, cls) {
  var chip = makeChip(name, kind, size);
  if (cls) chip.className = "uchip " + cls;
  $("#uploadFiles").appendChild(chip);
  uploadChips[name] = chip;
  return chip;
}
function markSending(name) {
  var chip = uploadChips[name]; if (!chip) return;
  chip.classList.add("sending"); chip.querySelector(".ustate").textContent = "indexing…";
}
function markChipDone(name, info) {
  var chip = uploadChips[name];
  if (!chip) chip = addChip(name, info.kind, null);
  chip.classList.remove("sending"); chip.classList.add("done");
  var st = chip.querySelector(".ustate");
  countUp(st, 0, info.chunks, function (v) { return Math.round(v) + " chunks"; }, 720);
  if (info.kind) chip.querySelector(".ukind").textContent = info.kind;
  glow(chip, "g");
}
function markChipRejected(name, reason) {
  var existing = uploadChips[name];
  if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
  var chip = el("div", "uchip rejected");
  chip.appendChild(el("span", "ukind", "skip"));
  chip.appendChild(el("span", "uname", name));
  chip.appendChild(el("span", "ustate", reason || "rejected"));
  chip.title = reason || "rejected";
  $("#uploadRejected").appendChild(chip);
  delete uploadChips[name];
  glow(chip, "r");
}
function renderClientRejection(list) {
  var box = $("#uploadRejected");
  (list || []).forEach(function (r) {
    var chip = el("div", "uchip rejected");
    chip.appendChild(el("span", "ukind", "skip"));
    chip.appendChild(el("span", "uname", r.name));
    chip.appendChild(el("span", "ustate", r.reason));
    box.appendChild(chip); glow(chip, "r");
  });
}
function renderUploadError(status, detail, is429) {
  var box = $("#uploadError"); box.hidden = false;
  box.className = "uploaderr" + (is429 ? " a" : "");
  box.textContent = "";
  box.appendChild(el("b", null, status ? "HTTP " + status + " — " : ""));
  box.appendChild(document.createTextNode(detail || "upload failed"));
  glow(box, is429 ? "a" : "r");
}
function setDropBusy(on) { $("#drop").classList.toggle("busy", !!on); }
function renderUploadResult(res) {
  var chunks = 0;
  (res.files || []).forEach(function (f) { markChipDone(f.name, f); chunks += Number(f.chunks || 0); });
  (res.rejected || []).forEach(function (r) { markChipRejected(r.name, r.reason); });
  uploadTotalChunks += chunks;
  if (chunks) bumpCorpus(chunks);
  $("#uploadSummary").textContent = (res.files || []).length + " file(s) · " + chunks + " chunks indexed · " + fmtInt(res.ms) + " ms";
  setStatus(chunks ? ("indexed " + chunks + " chunks — ask about them now") : "nothing new indexed", chunks ? "ok" : "err");
  scheduleVitals();
}
function handleFiles(list) {
  var arr = Array.prototype.slice.call(list || []);
  if (!arr.length) return;
  $("#uploadError").hidden = true;
  var accepted = [], rejected = [], total = 0;
  arr.forEach(function (f, idx) {
    var name = f.name || "file";
    var ext = "." + (name.split(".").pop() || "").toLowerCase();
    if (idx >= MAX_FILES) { rejected.push({ name: name, reason: "at most " + MAX_FILES + " files per upload" }); return; }
    if (SUPPORTED.indexOf(ext) < 0) { rejected.push({ name: name, reason: "unsupported type (md, txt, html, pdf, csv, tsv, xlsx)" }); return; }
    if (f.size > MAX_FILE) { rejected.push({ name: name, reason: "larger than 20 MB" }); return; }
    if (total + f.size > MAX_TOTAL) { rejected.push({ name: name, reason: "50 MB per upload limit reached" }); return; }
    total += f.size; accepted.push(f);
  });
  renderClientRejection(rejected);
  accepted.forEach(function (f) { var c = addChip(f.name, (f.name.split(".").pop() || "").toLowerCase(), f.size); c.classList.add("sending"); c.querySelector(".ustate").textContent = "queued"; });
  if (accepted.length) doUpload(accepted);
  else { setStatus("nothing to upload — see the reasons above", "err"); $("#uploadSummary").textContent = rejected.length + " file(s) pre-checked out"; }
}
async function doUpload(files) {
  setDropBusy(true);
  files.forEach(function (f) { markSending(f.name); });
  setStatus("indexing " + files.length + " file(s)… chunking, embedding, storing", "busy");
  var fd = new FormData();
  files.forEach(function (f) { fd.append("files", f, f.name); });
  try {
    /* PR #26 contract: the 202 body is an acknowledgement (job_id), not a result —
       poll /upload/status/{job_id} until indexing finishes, then render the result. */
    var ack = await jpostForm("/upload", fd);
    renderClientRejection(ack.rejected);
    renderUploadResult(await pollUpload(ack));
  } catch (e) {
    renderUploadError(e.status || 0, e.message, e.status === 429);
    files.forEach(function (f) { markChipRejected(f.name, e.status === 429 ? "not sent — busy" : "rejected"); });
    setStatus("upload failed: " + e.message, "err");
  } finally { setDropBusy(false); }
}

var drop = $("#drop"), fileInput = $("#fileInput");
drop.addEventListener("click", function () { fileInput.click(); });
drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
["dragenter", "dragover"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
["dragleave", "dragend"].forEach(function (ev) { drop.addEventListener(ev, function () { drop.classList.remove("over"); }); });
drop.addEventListener("drop", function (e) { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer && e.dataTransfer.files) handleFiles(e.dataTransfer.files); });
fileInput.addEventListener("change", function () { handleFiles(fileInput.files); fileInput.value = ""; });

export { renderUploadResult, renderClientRejection, renderUploadError };
