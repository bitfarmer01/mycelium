# Mycelium: required features

This document determines what Mycelium must build, what our accounts force, what strengthens the demo, what stays a slide and what is excluded. Dated 2026-10-16 14:25 EDT. It rests on `docs/product-goal.md`, the Context-Engineering mechanisms study (sections 2 and 6, ids F1 to M9) and the live sponsor spike (`sponsor-capabilities.md`, `results-*.json`). The one goal change that matters: "voice-first" is gone, "context layer" replaced it, and sentence 1 still says "spoken and on screen". Spoken output is necessary; voice input is not. Classification is by necessity; hours are a second axis and never change a class. The builder's ruling (13:58 EDT): voice stays in as a feature, spoken answers plus a voice way to ask, correct and reflect, but the product is not voice-first, so typed input is an equal path on the same screen and no other feature may depend on voice. The study and the spike files live in the kb-core scratchpad named in `pitch-features.md`; copy them into `docs/` before that directory is cleared.

## 1. What the keys can do today

| Key or service | Verified live | Blocked or unknown | Consequence |
|---|---|---|---|
| ANCHOR_API_KEY | Free tier, 204.99 credits (anc.account ANC-17..18); demonstration share_url in 2065 ms, 1-hour JWT, defaults 20 min / 5 min idle (ANC-19..31) | compile time unknown (ANC-15) | No goal sentence needs browsing. Adapter off; key unused by any necessary or required feature. |
| SURREAL_DB_ACCESS_TOKEN (Cloud PAT) | valid; org on Start, role owner, 0 instances, 1 free allowed (am.access AM-17..19) | no instance; LIVE SELECT untested on Cloud | Host is local Docker. Key unused. |
| Local SurrealDB 3.3.4 Docker | LIVE SELECT write-to-event 1.6 ms (sdb.live_select SDB-01..07) | HNSW index, RELATE, UPDATE push, python SDK: docs-only | The body. numpy cosine if HNSW fails; 2 s poll if push fails. |
| SURREAL_AGENT_MEMORY_URL, _CONTEXT, sp- key | absent | remember/recall/reflect/consolidate, /facts, /forget: "no endpoint configured" (AM-20, AM-21) | Every memory row is a plain table. All Agent Memory features blocked. |
| AWS_* (Bedrock us-west-2) | STS valid (AWS-01); Opus 4.5 "OK" in 1388 ms (aws.bedrock_claude AWS-02..04) | account ends about 2026-10-19 09:44 EDT; session creds may lapse sooner | Only verified sandbox auth. Health-check before every exam. |
| DAYTONA_API_KEY | Claude Code via Bedrock in a container, started 140 ms, answered 2966 ms, refused without creds (DAY-78..91); 3 daytona-small, wall 6670 ms (day.exam_burst3 DAY-108..151); autostop 15 min, 0 accepted (DAY-32..41) | VM 400 (DAY-31); Windows 403 (DAY-61..63); container fork 422 (DAY-42..49); pool 10 GiB (DAY-152..167); 4 vCPU / 8 GB / 10 GB each (DAY-56..60); Max OAuth untested (DAY-50) | Isolation is three disposable containers, no tree. Autostop 0, delete after. |
| ARIZE_API_KEY | key valid, space id derivable (ARZ-03..05); 4 hooks fire under headless `claude --print`, CHAIN + LLM spans readable in about 10.5 s (arz.hooks_headless ARZ-21..32) | hooks inside a sandbox not exercised; custom span attributes need otel code | Free trace per CLI call via `--settings`; hooked run 5959 ms (ARZ-26). |
| ELEVENLABS_API_KEY | Creator, 130,905 credits (EL-59..60); flash_v2_5 TTS 542 ms, eleven_v3 with tags 2585 ms (el.tts_latency EL-46..50); two labelled voices plus expressive_mode (EL-17..30) | audible switching unknown, needs a live call through an unbuilt custom-LLM proxy (EL-51..58); flash_v2_5 refused as agent base (EL-06) | Spoken output is plain TTS; voice input is Scribe speech-to-text (docs-only, same key), no agent. 1 credit per character (services.md §6) over a 60-word spoken tier of about 350 characters: about 370 answers from 130,905 credits (EL-59..60). |
| Claude Code CLI on the host (no Anthropic or OpenAI key) | headless `--print --output-format json` rc 0, provider firstParty, cache 24,345 created + 21,146 read (ARZ-26); `--json-schema` listed in `claude --help` on this machine at 14:20, unexercised | `--json-schema`, `--safe-mode`, `--tools ""` never exercised | Every model call; two sessions per question, about 45k cache tokens each. |

## 2. Necessary features, by goal sentence

### Sentence 1: citations, spoken and on screen, and the reading taken

Minimal truth: chunk ids from retrieval, citations that resolve to them, two or more readings with a named taken one, an audible answer naming a source and the reading, a clickable card. By the builder's ruling a voice way in is also a feature here: speech-to-text into the same endpoints, never a separate pipeline.

| Feature | CE | Rests on | On-screen proof | Hours | Fallback |
|---|---|---|---|---|---|
| One KNN top-8 for the taken reading's query, as numbered context | R1 retrieval half, T4 | local HNSW (server SDB-01..07; index docs-only); bge-large cached 1.2 GB | top-8 ids with cosine under the taken chip; KNN ms | 0.5 | numpy cosine over 2,685 x 1024; token overlap if the embedder fails |
| Cited synthesis under SLOT + CONTRACT: `{spoken, screen, claims[].chunk_ids}`, one-pass resolve check, strip uncited sentences | V3, slim V4, E3 citation half, T4 | host CLI firstParty (ARZ-26); `--json-schema` unexercised | "citations N/N resolved"; 0 dangling over the 29 | 2.0 | V7 "I found N passages" plus titles; abstention when nothing retrieved |
| Readings call: fixed roles {literal, intent, scope}, p vector, `taken`, `why`, `flip_condition`, disclosed aloud and on screen | R1 | host CLI; plain question and reading rows | three chips, one ((taken)), flip_condition beneath | 0.5 | one literal reading, p = 1. Roles stay fixed or sentence 2's prior has no support |
| Spoken tier: 60 words or fewer, one source by title, ends "I read that as X", flash_v2_5 | V2 | ElevenLabs TTS (EL-46..50) | time-to-first-audio; 60 words or fewer on all 29 | 1.0 | browser speechSynthesis of the same string. Also reads sentence 5's reflection |
| Answer card: screen tier, [N] to chunk card {title, link, snippet, cosine}, reading chips, typed input, play button | V4 screen, R1 chips | FastAPI page (section 3) | every [N] clicks through; one taken chip | 1.25 | renders from the HTTP response |
| Voice input: a hold-to-talk button sends audio to ElevenLabs Scribe speech-to-text and drops the transcript into the same typed `/ask`, `/correct` and `/reflect` path; the transcript is shown before it is sent | V5 standalone rewrite for follow-ups, V8 schema validation on the endpoints | ElevenLabs STT on the verified key (EL-59..60); the Scribe endpoint itself was not spiked, docs-only | transcript lands in the box, then the normal answer; "what did you learn today" works by voice | 1.0 | browser SpeechRecognition into the same box (0.25 h, no sponsor); typed input is always there |

### Sentence 2: every correction becomes a prior for the next person

A correction input, a shape key, an EMA prior, and prior-weighted selection shown on a same-shape question from another session. None makes a model call.

| Feature | CE | Rests on | On-screen proof | Hours | Fallback |
|---|---|---|---|---|---|
| Correction event: tap a chip writes an observation row and a ledger row {shape, correct, before, after, cause} | F4, R1, T4 | CREATE + LIVE SELECT (SDB-01..07); RELATE docs-only | "correction · shape · role to role · prior 0.33 to 0.53" within 1 s | 0.5 | JSON lines, prior in process. Same endpoints serve sentence 3 |
| Shape key: enum from the readings call, keyword cascade fallback, never null | R6 | readings call | shape on every question; 0 Unknown over the 29; paraphrase lands in the same shape | 0.25 | cascade, else "factual" |
| Shape prior `question_shape {prior[3], n}`: prior += 0.3 (onehot − prior) on correction, 0.1 toward the taken role otherwise, floor 0.05; three bars pushed live | R5, R4 | UPDATE + LIVE SELECT (UPDATE push docs-only) | 0.33 to 0.53 to 0.67; H falls from 1.585 bits | 1.0 | in-process dict to JSON. EMA, not spec §4's raw count |
| Prior-weighted re-ask: score = prior x p, argmax; a paraphrase under a visibly different session id opens on the corrected reading; no user field on the prior | R3, R5, T4 | readings call + SurrealDB read | corrected role taken on the paraphrase; session badge differs; spec check 7 (about 20 CLI calls) | 0.75 | V7 default = argmax prior. Flip needs p_wrong / p_right under 2.29 after one correction: planted ambiguous question or two corrections |

### Sentence 3: proven usefulness on a live feed

One bounded per-chunk number moved by confirm, correct and decay; a page that reorders without refresh and names the cause; the events; a visible tick.

| Feature | CE | Rests on | On-screen proof | Hours | Fallback |
|---|---|---|---|---|---|
| Bounded fitness per chunk: Beta(α,β) init (1,1); e > 0.5 adds 2(e−0.5) to α else 2(0.5−e) to β; fitness α/(α+β). Or spec §4's x1.1 / x0.9 clamp [0.5, 2.0] | F1 | plain fields (SDB-01..07) | 0.50 to 0.67 after a confirm (uncertainty 0.33 to 0.22); 0.50 to 0.33 after a correction; never leaves (0,1) | 1.5 | arithmetic only. Pick one form; print the [0.5, 2.0] mapping; align spec §4 and §7 |
| Ledger feed: one row per change {target, type, delta, before, after, cause, ts}; LIVE SELECT on the ledger table, not a view; ORDER BY fitness or fitness − 0.5·uncertainty; LIMIT 20 | F4, F3 corrected, F8 | LIVE SELECT 1.6 ms (SDB-01..07); relay in section 3 | top card changes within 1 s; before to after on every row | 2.0 | 2 s poll. pytest: a correction never raises rank (CE F3's alias rises at low counts) |
| Evidence events: `cites` edges per answer; confirm writes 1.0, correct writes 0.0 for every cited chunk | F1, F4 | plain SurrealQL | "cited 3 chunks"; one observation and one ledger row per tap | 0.5 | shares endpoints with sentence 2 |
| Stale tick: a button shrinks (α,β) toward (1,1) on evidenced chunks with no event since the last tick; ledger row type = decay; age badge display-only | F1 decay, F4 | one UPDATE | 0.67 to 0.66 per tick at 5% (CE) or 0.667 to 0.665 at 1%/day (spec); constant printed | 0.25 | slide states the rate. Restrict to α+β > 2 or 2,685 rows flood the feed |

### Sentence 4: rewrites only under a test, in sandboxes, above the noise, poison rejected

A slot plus a record that promotion followed a PASS, a generator, a disjoint held-out set, isolation with a visible sandbox id, a noise estimate and gate, a planted poison rejected visibly.

| Feature | CE | Rests on | On-screen proof | Hours | Fallback |
|---|---|---|---|---|---|
| Slot and promotion record: `prompts/slot.md` as {intent, process[], output{}} plus frozen sections; diff of the one changed section; candidate row {verdict, version}; promotion writes only the file; `ask` re-reads the slot at call time | G4, G5 core | filesystem; candidate rows | v1.0 to v1.1 only after PASS; slot hash unchanged after any veto; next answer prints the version | 1.25 | JSONL ledger |
| Candidate pool: baseline plus three fixed suffixes plus one composed pair, plus the poison; zero model calls | G3, I2 | none | 4 clean + 1 poison rows with axis and diff | 0.5 | none needed. Meta-prompt rewrite is supporting |
| Held-out ten: `question.split` frozen, disjoint from the 19 train and demo questions, hash printed; beats 1 to 3 from train | G1, E4 | the 29 labelled questions | "H = 10 of 29, 0 overlap"; "retrieval calls inside sandboxes: 0" | 0.5 | none needed |
| Exam burst: baseline x3 and each candidate once over H, one daytona-small per run, three wide, Claude Code via Bedrock, sandbox id on every row, deleted after | G1, G4, G5 | DAY-108..151, DAY-78..91, DAY-152..167, DAY-32..41 | three sandboxes started under 0.5 s each; id per row; count 0 after | 4.0 | runner = local Docker container, labelled. Ten `--print` sessions per run at about 45k cache tokens; exec timeout 150 s or more; `--safe-mode` unverified Fallback chain: refresh the workshop credentials in .env first (owner in section 8); if the pre-exam Bedrock health check still fails, runner = local, labelled on every row. |
| Spread gate: m1..m3 over H; s = max(max − min, bootstrap 95% half-width), printed; delta = mean_c − mean_b; PASS iff delta > s; band, dots, poisoned dot red below | G1, E4, G5 | numpy; runs from the row above | "baseline 0.71 ± 0.04 · candidate 0.78 · +0.07 > 0.04 · PASS" (CE wording; real numbers only when runs finish) | 1.0 | empty band, "designed, measured after the demo" |
| Lint veto and BLOCK card: forbidden regex (ignore the wiki, do not cite, you may invent, system:, URLs, tool syntax), required placeholders, frozen-section hash, tokens at most 1.25x; poison dies before any sandbox; card names the pattern | I1, I2, I3 | 20 lines of regex; red row via LIVE SELECT | "vetoed 3/3 poisoned, 0/3 clean"; pattern on the card; slot hash identical; 0 sandbox runs on poisons | 2.0 (0.5 in CE spine step 6) | local dict. Pick poisons the regex matches, or count lint and gate catches separately (the "30 days" poison passes lint) |

### Sentence 5: ask what it learned today, from its own memory

Rows written during the session carrying observation ids, and one Claude call that composes them into sentences each citing a row id.

| Feature | CE | Rests on | On-screen proof | Hours | Fallback |
|---|---|---|---|---|---|
| Lesson rows per correction, prior move, fitness band crossing, verdict and veto: {phase, change, rationale, contributor, ts, source_ids, observation_ids}; at reflect time derive rows from the observation and candidate tables if the lesson table is empty | M1, M4 minimal, V3 | SurrealDB (SDB-01..07) | "lessons today: N" equals the session's events; each row shows its observation ids | 0.75 | JSON lines behind the same helper. Lessons derive only from observations |
| Reflect call: SELECT today's rows into one `claude --print`, four headings, five sentences at most, one row id each; post-validator drops unresolved sentences | M1, E7 | host CLI firstParty (ARZ-26, 5959 ms) | "5/5 sentences cite a row id, 0 without, rows read: N"; click highlights rows | 0.5 | zero rows: fixed "nothing yet today", no call. CLI fails: render change + rationale per row. Timeout 20 to 30 s |

## 3. Required plumbing

- **Local Docker SurrealDB 3.3.4, schema, one-time Neon load.** Hard rule 2; Cloud has 0 instances (AM-17..19); only local is verified (SDB-01..07). Tables: chunk, source, question, reading, observation, ledger, question_shape, candidate, lesson, config. HNSW first, numpy if it fails. The Neon export is not on disk and no Neon credential is in .env: obtain it first. `pip install surrealdb websockets`. 1.0 h.
- **Runtime query embedder.** The corpus vectors are bge-large 1024-d; same model and prefix at runtime. Cached and importable in the kb-core venv. Self-test: embed one sentence, assert 1024 dims. Fallback token overlap (BM25 FULLTEXT docs-only, clamps terms on small corpora). 0.25 h.
- **Claude Code CLI wrapper.** Hard rule 1. I5 four schemas with jsonschema.validate, retry at most 2 (`--json-schema` unexercised; if the flag is rejected, extract the first balanced JSON object from stdout, validate it in Python, one error-fed retry, then V7); R8 per-call timeout for the `--print` hang trap; V7 never a silent turn; I7 two buckets so gate calls never starve the answer path (sandbox calls run on Bedrock, not the Max quota). Ten-minute flag check. Fallback `CLAUDE_CODE_USE_BEDROCK` on the host (AWS-02..04), then V7. 2.75 h (I5 1.5, R8 0.5, V7 0.5, I7 0.25).
- **Local HTTP surface.** FastAPI `/ask`, `/correct`, `/reflect`, `/improve`, static page, websocket or SSE relay of LIVE SELECT ledger events: the browser cannot hold root ws credentials, and sponsor §4 says DEFINE API defaults to PERMISSIONS FULL. websockets not installed. Fallback 2 s poll. 1.25 h.
- **Voice path plumbing.** The voice feature is STT in and TTS out over the same HTTP endpoints as typed input, so it needs only the ElevenLabs key already in .env plus microphone permission in the page. The custom-LLM SSE proxy over `claude --print` (about 3 h, public HTTPS, function calling) is required only if the conversational-agent upgrade in section 4 is chosen; nothing else depends on it. 0.25 h beyond the STT row.
- **Sponsor adapters (hard rule 5).** Daytona to a local Docker container per candidate (a bare subprocess makes "isolated sandboxes" false; print `runner=`); Arize to the wrapper's per-call JSON record (ARIZE_LOG_FILE is a verbose log, not spans); Agent Memory to plain tables; ElevenLabs to speechSynthesis; Anchor off. Each writes live or fallback to the ledger. 1.0 h.
- **Bedrock auth inside sandboxes.** Only Bedrock answered in a container (DAY-78..91); Max OAuth untested (DAY-50). Env file {CLAUDE_CODE_USE_BEDROCK=1, AWS_REGION=us-west-2, keys, session token, ANTHROPIC_MODEL=us.anthropic.claude-opus-4-5-20251101-v1:0} uploaded, sourced, deleted; negative control reads "Invalid API key EXIT=1"; a 5-token Converse before each exam flips the runner to local if creds lapsed. Say "Bedrock, not Max". 0.25 h.
- **Runner adapter.** One interface over Daytona or local Docker; `runner` and `sandbox_id` on every run row; same containment flags both paths; no DB credentials or production slot in the sandbox env. 0.5 h.
- **Evidence pack per held-out question.** Numbered context from one host KNN plus slot plus CONTRACT, uploaded with `fs.upload_file`; the sandbox runs synthesis only. Spec §4 "cached evidence, no retrieval". 0.5 h.
- **Deterministic scorer E3.** Two or three must-mention terms per held-out question (about 15 min for ten), every [N] resolves, length bounds; no model call; immune to judge injection. Forced because E1 costs 2 h plus 30 extra `--print` calls per baseline on expiring credentials; CE §6: "use E3 and say so". 1.0 h.
- **Config record.** One row with every constant (Beta step, decay rate, prior 0.3 / 0.1 / floor 0.05, commit 0.8 / margin 0.2, K = 3, lint list, 60-word cap, held-out ids, fitness mapping), rendered on screen. Inside F4's 0.5 h.
- **Slot and CONTRACT files.** `prompts/slot.md` the only mutable file; CONTRACT a constant in code; sha256 of the slot in the ledger before and after promotion (hard rule 3). 0.25 h.

## 4. Build if time remains

- Per-reading KNN x3 with R2 overlap ambiguity, 1.5 h: a measured ambiguity number beside the disclosure.
- R3 / R4 / V1 band script and entropy bits, 0.75 h: label H as over self-reported p (CE §5 item 10).
- I4 citation retry loop 1.5 h, V4 contradictions and gaps: stronger citations; one more 45k-token session per retry.
- T1 MMR (tau from a histogram) and T5 envelope, 1.0 h: more distinct sources; T5 idle at k = 8.
- V9 glyphs 0.5 h, T3 method per citation 1 h: card polish.
- R7 kNN of corrected questions, brute-force cosine, 1 h: widens the re-ask flip margin.
- R9 re-answer under the corrected reading, tap path, 0.25 to 0.5 h: spec beat 2.
- T2 rerank cos x (0.5 + 0.5 f), 0.5 h: first feed extra; "was #7, now #2" is expected, not measured.
- Source fitness mean, `cited_count` / `retrieved_count`, 0.25 to 0.5 h: drop the 0.45 nudge and quadrant badge.
- Age badge from `timestamp`, display-only: supports the stale tick.
- Meta-prompt candidate from the worst three train questions (needs a 19-call train baseline and terms for 29): a self-authored rewrite.
- G5 lifecycle extras about 1 h; G2 booleans, G6 counter, I6 approval 2.25 h: a more convincing PASS; I6 weakens "rewrites its own".
- Arize trace per CLI call via `--settings`, 0.25 h (ARZ-21..32): hooks add wall clock that is not isolated (ARZ-27 5959 ms with hooks on the host vs DAY-78..91 2966 ms without, inside a container); vendor the plugin clone. E6 usage logging from `--output-format json`.
- Reflection rendered from rows when the CLI fails, 0.25 h; spoken reflection via flash_v2_5, 0.25 h, about 400 credits per five sentences.
- Gap lessons from `gaps[]` or abstain rows, 0.5 h; M2 "2/5" promotion and M4 coverage edges, 1.75 h: plant two same-shape corrections; skip M4's "coverage-1 not spoken" rule.
- Conversational-agent upgrade of the voice feature: ElevenLabs agent widget over the custom-LLM SSE proxy, barge-in, and the second Reflector voice with expressive tags (config verified EL-17..30, audibility unknown EL-51..58), about 3 h plus public HTTPS: richer voice, same endpoints behind it.

## 5. Slide only

- Audible mid-sentence voice switching (Inner voice as two voices in one utterance): needs a live call through the unbuilt proxy (EL-51..58); until then the reflection is read in one voice by TTS.
- Spoken correction as an agent server tool `correct(note)`, 4.5 h on the agent path: the voice feature's STT-into-`/correct` route covers the beat without it.
- E1 judge gate, verified flags per citation, groundedness evidence into fitness, multi-round S-G1 / S-G2: 2 h plus 30 judge calls per baseline at about 45k cache tokens; CE §6 step 8 does not fit.
- F2 source trust, 1 h: redundant with F1's source mean; its only distinct input is E1's blame.
- S-F1 trend arrows, 1 h: a 10-window never fills; the first miss is exactly −0.10 (CE §5 item 12).
- S-T1 hybrid BM25 + RRF, 1.5 to 2.5 h: needs gold page ids; BM25 clamps terms on small corpora.
- S-V3 audited spoken rendering, 1 to 1.25 h: V2 gives the subset property by construction.
- M6 learning line, 0.5 h: needs E4 start and end scores from the baseline runs; never print the illustrative +0.09.
- M3 merge and cadence, M5 contradicts edge, S-M1: volumes a demo never produces.
- S-F3 live map (2.5 to 3 h), S-F4 surprise rows, S-R5 sparkline, S-V1 degradation table (2.5 h).

## 6. Excluded or blocked

| What | Why | Unblock |
|---|---|---|
| Daytona VM fork and family tree (fork, /forks, /parent, /ancestors, pause/resume) | refused: VM 400 (DAY-31); Windows 403 below Tier 3 (DAY-61..63); container fork 422 (DAY-42..49) | Daytona enables linux-vm for the org or Tier 3 |
| Every Agent Memory feature: remember/recall/reflect/consolidate, "the diff it just learned", belief chain, uncertainty records, /lookup, standing rules, "what it knows ranked", trace ranking, recall hooks, /chat | blocked: no URL, context or sp- key (AM-20..21); org on Start with 0 instances (AM-17..19). Off-target anyway: Gemini-extracted entities, not wiki readings; /reflect and /chat run on the deployment model, against hard rule 1 | Owner subscribes, deploys a context, adds the three env vars, runs `run.py spike surreal`. Then a store behind the adapter, never the reflector |
| Audible ElevenLabs voice switching (two voices mid-sentence) | unknown (EL-51..58): needs a live call through the unbuilt proxy; simulate-conversation is text-only, removed 31 Oct 2026. The voice feature does not depend on it | proxy on public HTTPS, public agent, one live call |
| Agent on ElevenLabs' built-in LLM; thumbs sendFeedback; Procedures; doubt ledger; knowledge-base RAG | built-in LLM contradicts hard rule 1; thumbs cannot name a reading; RAG re-embeds with e5_mistral and has no chunk ids, so citations would be false by construction | proxy first; RAG never |
| Anchor Show-me learning, candidates that browse | no goal sentence; spec marks Anchor a stretch; compile time unknown (ANC-15). No necessary or required feature uses ANCHOR_API_KEY: the key is idle, the adapter off | one timed human demonstration and a goal sentence asking for it |
| Max-plan OAuth inside sandboxes | untested, no token (DAY-50); only Bedrock answered (DAY-78..91) | `claude setup-token`, inject into one container, rerun the DAY-78 probe |
| Arize tracing of `--print` inside sandboxes | not exercised; host only (ARZ-21..32) | upload hooks settings and ARIZE env into one sandbox |
| Vendor-LLM optimizers and judges: Arize Prompt Optimization, PromptLearningOptimizer, online evals, Alyx, hosted MCP; ElevenLabs testing badges, Architect | each runs its own model, against hard rule 1; Alyx answers from traces, not rows | only local scoring with `update_evaluations` write-back |
| SurrealDB Cloud as host | 0 instances, untested; LIVE SELECT verified locally only | spend the one free instance, rerun the spike |
| DEFINE EVENT, DEFINE API, table views, CHANGEFEED, SELECT VERSION | docs-only; events skip permission checks; LIVE SELECT on a view not stated (sponsor §2, DEFINE EVENT and table-views rows); the F4 ledger already shows before and after | ten-minute local spike if wanted |
| E6 full span attributes, S-R2 prior rules, S-F2 rank formulas, S-T2 graph hop, Daytona MCP self-spawn, reflect inside a sandbox | otel code contradicts the no-instrumentation path; R5's EMA is one line; two rank formulas today is a mistake; no entity edges; MCP auth is a browser login; a sandbox binds reflection to expiring creds | not needed |

## 7. Build order and dependencies

Clock: 14:25 EDT, submission 15:30, about 65 minutes. The CE spine's 5.25 h for steps 1 to 7 assumed the schema, server tools and feed page existed; the repo has one docs-only commit. Hours are the sources' figures.

1. **Locate the corpus (blocking).** No corpus export is on disk. Found 14:25, corrected 14:45: kb-core's live database is AlloyDB (`DATABASE_URL` in `~/Developer/kb-core/.env`; the `NEON_DATABASE_URL` there is a leftover and is not used). Neon is not used anywhere. The one-time export to `data/corpus.jsonl` runs from the mycelium venv with psycopg.
2. **Substrate (1.25 h, cum 1.25).** Docker SurrealDB, schema, load 2,685 chunks and 29 questions, HNSW or numpy, 10/19 split with hash, embedder self-test, config record.
3. **Packs and scorer (1.5 h, cum 2.75).** Host KNN per held-out question, slot and CONTRACT files with sha256, E3 terms and scorer, Bedrock health check. Needs 2.
4. **Launch baseline x3 in the background (0.25 h plus wall clock, cum 3.0).** Three daytona-small, autostop 0, exec timeout 150 s or more, Bedrock env file; ten synth calls per run at 3.1 to 6.3 s each; score on return, delete. CE §6: launch now or the gate is a design, not a screen. Needs 3; runs during 5 to 10.
5. **CLI wrapper (2.75 h, cum 5.75).** I5, R8, V7, I7, hooks file, flag check.
6. **Reflex answer path (3.0 h, cum 8.75).** Readings call, one KNN, cited synthesis with resolve-and-strip. Needs 2 and 5.
7. **Screen and voice (4.5 h, cum 13.25).** FastAPI, relay, answer card, spoken tier with speechSynthesis fallback, Scribe STT button into the same endpoints. Needs 6.
8. **Corrections (2.25 h, cum 15.5).** Tap event, shape key, EMA prior, re-ask under a second session id; spec check 7 while building. Needs 7.
9. **Feed (4.75 h, cum 20.25).** Beta fitness, ledger feed, cites and confirm events, decay tick, rank pytest. Needs 7 and 8.
10. **Growth (8 h, cum 28.25).** Slot record, decorator pool, lint veto and BLOCK card (the one growth beat that reproduces deterministically), runner adapter, exam burst orchestration (4.0 h; the create, upload, exec, cleanup pattern exists in `checks_daytona.py`), spread gate as a numpy script once step 4 lands. Needs 3, 4, 5.
11. **Reflect (1.25 h, cum 29.5).** Lesson rows with observation derivation, one reflect call with the post-validator. Needs 8 and 9.

Before 15:00 nothing past step 2 fits, and step 4 only if the corpus appears in the first minutes. If sentence 1 must show first, the order is 1, 2, 5, 6, 7, then 3, 4, 8, 9, 10, 11. If the gate's numbers are not in, the panel and slide carry exactly: "candidates run in Daytona sandboxes; a candidate is accepted only when it beats the baseline by more than the run-to-run spread; the poisoned candidate is vetoed before any sandbox by the lint gate (shown); the numerical gate ran offline on 10 held-out questions: designed, measured after the demo." Sandboxed Claude Code runs on Bedrock, not Max. Beat 3 becomes a scripted second ask under a second session id if step 8 is unfinished. Do not claim retention, transfer, emergence, or p < 0.05 at n = 10.

## 8. Decisions for a human

- Gate score: E3 deterministic, said so on the slide, or the E1 judge (2 h plus 30 extra calls per baseline on credentials ending about 2026-10-19 09:44 EDT).
- Fitness form: Beta(α,β) with a printed [0.5, 2.0] mapping, or the spec's x1.1 / x0.9 clamp; decay 5% per tick (CE) or 1% per day (spec). Spec §4 and §7 change either way.
- Voice feature shape: Scribe STT plus TTS over the typed endpoints (about 1 h; STT untested with our key; browser SpeechRecognition is the 0.25 h fallback), or the conversational agent over the SSE proxy with the second voice (about 3 h plus public HTTPS).
- Max-plan auth in a sandbox: try `claude setup-token` plus the DAY-78 probe, or accept Bedrock and name it.
- Anchor: cut outright (no feature uses the key) or keep the stretch slide.
- Daytona fallback: local Docker container per candidate, or a bare subprocess with "not isolated" printed.
- Corpus: resolved. One-time export from kb-core's AlloyDB (`DATABASE_URL` in kb-core's .env) to `data/corpus.jsonl`; no Neon anywhere. Still open: where the 29 labelled questions live.
- Candidate pool: decorators only, or the meta-prompt with its 19-call train baseline and terms for all 29.
- Promotion: automatic on PASS (spec beat 4) or behind an I6 approval click.
- Spec draft: amend §1 ("voice-first") and §5 (widget) to match the goal; fill §6 from sections 2 and 3 here.
