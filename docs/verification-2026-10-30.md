# Verification results — final cross-workstream integration (2026-10-30)

Closes the build spec's Verification section (todo_JQBOoEL8). All levels were run
against a booted stack in the repo sandbox: SurrealDB **v3.3.5** (binary), schema
applied, demo corpus ingested (**284 chunks / 48 sources / 29 questions** from
`data/docs`), and the app server on :8787 with `scripts/fake_claude.py` on PATH as
the model double (the spec's rule that a demo never depends on a live model
credential).

## Level 1 — automated checks

- Full pytest with `SURREAL_URL` set (integration included): **518 passed, 1 skipped**
  (`1 skipped` = the corpus-guard leg of a retrieval test; the corpus was ingested,
  so the guard did not fire).
- `ruff check` clean and `ruff format --check` clean on every file this PR touches.
- `scripts/smoke.sh` against the booted stack: **ALL SIX BEATS PASS** —
  beat 1 ask with `citations_resolved 1/1`; beat 2 correction moved the prior
  `0.256 → 0.472` with 3 ledger rows; beat 3 paraphrase learned after a second
  correction; beat 4 `/improve` scored 3 with 3 pattern vetoes; beat 5
  `slot_version` consistent between `/ask` and `/vitals`; beat 6 `/reflect`
  returned 5 sentences with row ids.

## Level 2 — cross-workstream contracts and visual evidence

- Citation payload: an integration test seeds a question through the live `/ask`
  and asserts the answer carries every field the console renders (`chunk_id`,
  `title`, `link`, `snippet`, `cosine`, `n`), resolved `1/1`; the console fixture
  (mock mode) is pinned to the same field names, so demo mode cannot drift from
  the API.
- Learning contract: `/learning/priors` serves the exact prior the ask/correct
  path just wrote.
- Visual evidence (browser, 1440×900, against the tested head SHA):
  - `tc-1-before.png` / `tc-1-after.png` / `tc-1-upload-flow.webm` — the console
    upload flow: the 202 acknowledgement, the polled `GET /upload/status/{job_id}`
    result, and the summary line ("1 file(s) · 1 chunks indexed · 163 ms").
    Verdict **pass** — the polled result text was asserted on the live DOM before
    the capture.
  - `tc-2-ask-citations.png` — the ask with its citation rendered.
    Verdict **pass** — captured after waiting on the answer text.

## Level 3 — end-to-end demo path

`tests/test_e2e_demo.py` runs the demo beats against the live DB: ask with
citations → correct → prior moves on `/learning/priors` → exam burst scored by the
test-double sandbox provider (`FakeSandboxProvider`, no live Bedrock credentials —
per the spec's open question, live exam evidence still waits on a fresh
credential decision) → burst persisted to `/exam/history`. Every external-service
down path is covered in the same run:

- **claude CLI down** → `/ask` returns HTTP 200 with a speakable abstain
  (`cli_unreachable`), recorded in the ledger.
- **ElevenLabs down/absent** → `/tts` degrades to a clean 502; the console's
  `audio.js` routes playback errors to `speechSynthesis` and flips the visible
  fallback badge (static wiring pinned; the badge itself was dogfooded in PR #14).
- **SurrealDB KNN query failing** → the NumPy cosine scan takes over and
  citations still resolve (top-k membership asserted under hybrid scoring).
- **Graceful shutdown / readiness** → `/healthz` always 200; `/readyz` 503 when
  the DB is unreachable (its test now actually stubs the surface the route
  resolves — see fixes below).

## Orchestrator addendum — type::thing / engine version

- The pinned spec's addendum says "SurrealDB 4", but no 4.x release exists; the
  current release (and the one installed here) is **v3.3.5**. The addendum's
  intent is still fully verified: the installed engine **rejects `type::thing`**
  outright (the e2e test asserts the statement errors), no call site remains in
  any shipped surface (`core/`, `app/`, `scripts/`, `infra/`), and the real
  write paths — `write_ledger`, `write_lesson`, prior UPSERT/`get_prior` —
  succeed and read back on the booted engine. Voice prefs were already on
  `type::record` (F4's migration).
- This boot also caught a latent bug the addendum's verification would otherwise
  have hidden: `core/db.py` cached connections by `id(loop)`, which is only
  unique among live objects — a garbage-collected loop's slot could hand a new
  loop a connection bound to the dead loop's closed client. Fixed with a weakly
  keyed cache plus one reconnect retry on stale transports (statement errors
  still raise unchanged).

## Bugs found and fixed by this integration pass

1. `core/db.py` — per-loop connection cache keyed by `id(loop)` (stale-client
   poisoning across loops); surfaced as 503s on `/learning/priors` and empty
   write results under pytest-asyncio's short-lived loops.
2. `infra/schema.surql` — `ledger_agg` (feature 37's compaction aggregates) was
   never defined, so `/learning/priors` 503'd on every fresh install until a
   first compaction ran.
3. `scripts/smoke.sh` — the CHUNKS probe called `py -c`, but `py()` prefixes
   `python3 -`, which executes *stdin* as the program; the `-c` code never ran
   and the probe silently returned an empty string. The six-beat smoke could
   never have passed as written on a live boot.
4. `tests/test_health.py` — the module-missing test replaced `sys.modules["core.db"]`,
   but the route's `from core import db` resolves the cached package attribute;
   the test passed only when the real DB happened to be down.
5. `tests/test_e2e_demo.py` — the httpx client fixture was re-entered across
   blocks (closed-client RuntimeError); record-id read-backs compared String to
   RecordId; the numpy-fallback test asserted rank 1 under hybrid scoring; the
   `type::thing` regression scan tripped on its own probe string.
6. Console upload polling (addendum item 2): `upload.js` rendered the 202
   acknowledgement as the ingest result. `api.js` now polls
   `GET /upload/status/{job_id}` with bounded exponential backoff and renders
   the polled result; static tests pin the contract.

## What remains open (not a merge blocker)

- Live exam bursts need fresh Bedrock credentials (the workshop account the
  services doc recorded ended 2026-10-19) — exam features run against the
  test-double provider, per the spec's stated fallback.
- Live HNSW measurement (feature 19's adopt-or-fallback decision) remains on the
  NumPy path; the evaluation harness exists (`scripts/hnsw_eval.py`).
