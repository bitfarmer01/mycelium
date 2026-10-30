import { $, el, num } from "./dom.js";
import { predictPrior } from "./prior_math.js";

/* ---------------------------------------------------------------- correction preview (feature 35) --
   Shows the prior delta a correction would apply BEFORE it is submitted.
   Two sources of truth are shown and must agree to float precision:
   the server's /learning/preview response and the client calc from
   predictPrior() — the parity test in tests/test_correction_preview.py holds them equal. */

var ROLES = ["literal", "intent", "scope"];
var panel = null;

function dismissPreview() {
  if (panel) { panel.remove(); panel = null; }
}

function row(role, before, after, isTarget) {
  var r = el("div", "pvrow" + (isTarget ? " target" : ""));
  r.style.cssText = "display:flex;justify-content:space-between;gap:12px;padding:3px 6px;border-radius:6px"
    + (isTarget ? ";background:rgba(255,255,255,.06);font-weight:600" : "");
  r.appendChild(el("span", "pvrole", role));
  r.appendChild(el("span", "pvvals", num(before, 4) + " → " + num(after, 4)));
  r.appendChild(el("span", "pvdelta", (isTarget ? "Δ " : "  ") + (after - before >= 0 ? "+" : "") + num(after - before, 4)));
  return r;
}

function showPreview(pv, onSubmit, onDismiss) {
  dismissPreview();
  var idx = ROLES.indexOf(pv.to_role);
  // client calc from the mirrored prior math — must match pv.prior_after to float precision
  var predicted = predictPrior(pv.prior_before, idx, pv.rate);
  var parity = predicted.every(function (v, i) { return v === pv.prior_after[i]; });

  panel = el("div", "preview");
  panel.style.cssText = "margin:8px 0 12px;padding:10px 12px;border:1px solid rgba(255,255,255,.14);border-radius:10px;display:flex;flex-direction:column;gap:4px";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Correction preview");
  var head = el("div", "pvhead");
  head.appendChild(el("b", null, "correction preview"));
  head.appendChild(el("span", "hint", "shape: " + String(pv.shape) + " · confidence " + num(pv.confidence, 2) + " · rate " + num(pv.rate, 3)));
  panel.appendChild(head);
  panel.appendChild(el("div", "hint", "the prior will move " + String(pv.from_role || "—") + " → " + String(pv.to_role) + ". Nothing is written until you confirm."));
  pv.prior_before.forEach(function (b, i) {
    panel.appendChild(row(ROLES[i], b, predicted[i], i === idx));
  });
  if (!parity) {
    // unreachable if the mirror stays in lockstep; surface it rather than hide it
    panel.appendChild(el("div", "hint", "client calc differs from the server — showing server values"));
  }
  var act = el("div", "pvact");
  act.style.cssText = "display:flex;gap:8px;margin-top:8px";
  var yes = el("button", "btn sm", "Submit correction");
  yes.type = "button";
  yes.addEventListener("click", function () { dismissPreview(); onDismiss(); onSubmit(); });
  var no = el("button", "btn sm ghost", "Cancel");
  no.type = "button";
  no.addEventListener("click", function () { dismissPreview(); onDismiss(); });
  act.appendChild(yes); act.appendChild(no);
  panel.appendChild(act);

  var chips = $("#chips");
  if (chips && chips.parentNode) chips.parentNode.insertBefore(panel, chips.nextSibling);
  else document.body.appendChild(panel);
  yes.focus();
}

export { showPreview, dismissPreview };
