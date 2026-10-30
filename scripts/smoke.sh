#!/usr/bin/env bash
# Six-beat demo smoke over HTTP. Usage: BASE=http://localhost:8787 scripts/smoke.sh
set -euo pipefail
BASE="${BASE:-http://localhost:8787}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
Q1="${Q1:-what test runner does voiceai use}"
Q3="${Q3:-which tool runs the tests in the voiceai repo}"
post() { curl -sf -m 300 -XPOST "$BASE$1" -H 'content-type: application/json' -d "${2:-{\}}"; }
py() { python3 - "$@"; }
ROLES='["literal","intent","scope"]'

echo "== beat 1: /ask"
post /ask "{\"text\":\"$Q1\",\"session_id\":\"smoke-s1-$$\"}" > "$TMP/b1.json"
CHUNKS=$(curl -sf "$BASE/vitals" | python3 -c 'import json,sys;print(json.load(sys.stdin)["counts"].get("chunk") or 0)' 2>/dev/null || echo 0)
py "$TMP/b1.json" "$CHUNKS" <<'P'
import json,sys
d=json.load(open(sys.argv[1])); chunks=int(sys.argv[2])
cr=d["citations_resolved"]; n,m=(int(x) for x in str(cr).split("/"))
assert n==m, f"citations_resolved {cr}"
assert d.get("spoken"), "empty spoken"
if m==0:
    assert chunks==0, f"0/0 with {chunks} chunks"
    print("WARNING: corpus empty, citations_resolved 0/0 (abstention)")
print(f"PASS beat1 citations_resolved={cr} taken={d['taken']} qid={d['question_id']}")
P
QID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["question_id"])' "$TMP/b1.json")
TAKEN=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["taken"])' "$TMP/b1.json")
TO=$(python3 -c 'import sys;r=["literal","intent","scope"];t=sys.argv[1];print("intent" if t!="intent" else "literal")' "$TAKEN")

echo "== beat 2: /correct $QID $TAKEN -> $TO"
post /correct "{\"question_id\":\"$QID\",\"to_role\":\"$TO\"}" > "$TMP/b2.json"
python3 - "$TMP/b2.json" "$TO" <<'P'
import json,sys
d=json.load(open(sys.argv[1])); to=sys.argv[2]; i=["literal","intent","scope"].index(to)
b,a=d["prior_before"][i],d["prior_after"][i]
assert a>b, f"prior {to} {b}->{a}"
ids=d.get("ledger_ids") or d.get("ledger") or []
assert len(ids)>=1, "no ledger ids"
print(f"PASS beat2 prior[{to}] {b:.3f}->{a:.3f} ledger={len(ids)}")
P

echo "== beat 3: paraphrase in a new session"
flip() { post /ask "{\"text\":\"$Q3\",\"session_id\":\"smoke-s3-$$-$1\"}" > "$TMP/b3.json"; python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["taken"])' "$TMP/b3.json"; }
T3=$(flip a)
if [ "$T3" != "$TO" ]; then
  echo "beat3: first try took $T3, applying a second correction"
  Q3ID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["question_id"])' "$TMP/b3.json")
  post /correct "{\"question_id\":\"$Q3ID\",\"to_role\":\"$TO\"}" > /dev/null
  T3=$(flip b)
fi
[ "$T3" = "$TO" ] || { echo "FAIL beat3 taken=$T3 want $TO"; exit 1; }
echo "PASS beat3 taken=$T3"

echo "== beat 4: /improve"
post /improve > "$TMP/b4.json"
python3 - "$TMP/b4.json" "$DIR/data/baseline_runs.json" <<'P'
import json,sys,os
d=json.load(open(sys.argv[1])); c=d["candidates"] if isinstance(d,dict) else d
v=[x for x in c if x.get("status")=="vetoed_prelint" and "pattern" in (x.get("veto_reason") or "")]
assert len(v)>=3, f"only {len(v)} pattern vetoes: {[(x.get('status'),x.get('veto_reason')) for x in c]}"
if os.path.exists(sys.argv[2]):
    s=[x for x in c if x.get("status")=="scored" and x.get("verdict")]
    assert s, "baseline exists but no scored candidate with verdict"
    print(f"  scored={len(s)}")
else:
    print("WARNING: no data/baseline_runs.json, scoring skipped")
print(f"PASS beat4 vetoed_prelint={len(v)} of {len(c)}")
P

echo "== beat 5: slot_version"
post /ask "{\"text\":\"$Q1\",\"session_id\":\"smoke-s5-$$\"}" > "$TMP/b5.json"
curl -sf "$BASE/vitals" > "$TMP/v.json"
python3 - "$TMP/b5.json" "$TMP/v.json" <<'P'
import json,sys
a=json.load(open(sys.argv[1]))["slot_version"]; v=json.load(open(sys.argv[2]))["slot_version"]
assert a is not None and a==v, f"ask {a} vitals {v}"
print(f"PASS beat5 slot_version={a}")
P

echo "== beat 6: /reflect"
post /reflect > "$TMP/b6.json"
python3 - "$TMP/b6.json" <<'P'
import json,sys
d=json.load(open(sys.argv[1])); s=[x for x in d.get("sentences") or [] if x.get("row_ids")]
assert s, f"no sentence with row_ids: {str(d)[:400]}"
print(f"PASS beat6 sentences={len(s)}")
P
echo "ALL SIX BEATS PASS"
