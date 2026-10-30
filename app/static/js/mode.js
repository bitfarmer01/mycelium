import { clone } from "./dom.js";
import { FX } from "./fixtures.js";

/* ---------------------------------------------------------------- mode + fixtures -- */
var QS = String(location.search || "");
var MOCK = /(^|[?&])mock=1(&|$)/.test(QS);
var AUDIT = /(^|[?&])audit=1(&|$)/.test(QS);
var MOCK_VITALS = clone(FX.vitals);

export { MOCK, AUDIT, MOCK_VITALS };
