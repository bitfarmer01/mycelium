/* ------------------------------------------------------------------ inline fixtures --
   Real payloads captured from the running server (data/ui-fixtures.json). Mock mode
   answers every call from these; nothing here is a network request. ------------------ */
var FX = {
  ask: {
    question_id: "question:s7jjw0ouju6jw5f1ww26",
    session_id: "fixture-session-1",
    shape: "how-to",
    readings: [
      { role: "literal", paraphrase: "What is the standard, best-practice structure for a sales discovery call: its stages, timing and the questions to ask?", p: 0.35, posterior: 0.35 },
      { role: "intent", paraphrase: "How does our organization tell reps to run a discovery call, according to our own playbook or methodology in the knowledge base?", p: 0.45, posterior: 0.45 },
      { role: "scope", paraphrase: "How should a particular kind of discovery call be structured, such as a customer-research or product discovery interview, or a consulting scoping call, rather than a sales qualification call?", p: 0.2, posterior: 0.2 }
    ],
    taken: "intent",
    taken_reading: {
      role: "intent",
      paraphrase: "How does our organization tell reps to run a discovery call, according to our own playbook or methodology in the knowledge base?",
      assumption: "Mycelium answers questions from a company corpus, so the user wants the documented internal process rather than generic advice.",
      query: "discovery call playbook agenda stages qualification framework internal guidelines",
      p: 0.45
    },
    why: "The question was asked inside the Mycelium project, which is built to answer from a company corpus. That makes it likely the user wants the organization's documented discovery-call method, not generic sales advice. Generic best practice is the close second reading and the fallback.",
    flip_condition: "If the corpus has no internal discovery-call playbook or sales methodology document, switch to the literal reading and give general best-practice structure.",
    spoken: "The wiki does not cover this. I didn't get any wiki pages with this question, so I can't say how a discovery call should be structured. I read that as How does our organization tell reps to run a discovery call, according to our own playbook or methodology in the knowledge base.",
    screen: "**Reading:** I took this as a request for the recommended steps or agenda for a sales or customer discovery call.\n\n**Answer:** The wiki does not cover this. No wiki pages came with this question, so I have no sources to answer from or cite.\n\nTo get an answer, share the relevant wiki pages, such as a sales playbook, a discovery call template or call notes, and I'll answer from them with citations.",
    claims: [], contradictions: [],
    gaps: [
      "No wiki pages came with the question, so nothing covers how to structure a discovery call.",
      "No sales playbook, discovery call template or call notes were available."
    ],
    confidence: "low",
    citations: [],
    citations_resolved: "0/0",
    prior: [0.3333333333333333, 0.3333333333333333, 0.3333333333333333],
    entropy_bits: 1.5128876215181604,
    slot_version: "1.0",
    ms: 30548,
    audio_url: "/tts?text=The%20wiki%20does%20not%20cover%20this."
  },
  askCited: {
    citations_resolved: "2/2",
    screen: "A discovery call follows the BS WISP skeleton [1]. Send a same-day proposal [2].",
    citations: [
      { n: 1, chunk_id: "chunk:ab12", title: "Discovery call playbook", link: "file:///docs/discovery.md", snippet: "A discovery call follows the BS WISP skeleton, a six-stage structure that keeps the rep in listen mode.", cosine: 0.82 },
      { n: 2, chunk_id: "chunk:cd34", title: "Proposal guide (p. 2)", link: "https://example.org/proposal.pdf", snippet: "Promise a same-day proposal and confirm the next step before the call ends.", cosine: 0.77 }
    ]
  },
  reflect: {
    sentences: [
      { text: "Sources that rose or fell: none moved, because each of today's four user corrections down-weighted 0 cited chunks.", row_ids: ["lesson:yj7ahkvbxkhbl7vca4ql", "lesson:jhrxu9edp4adtu8vi6w3", "lesson:8o7w63wmnvoc3t1ubrhj", "lesson:y9bv9zry8huenx4ys71r"] },
      { text: "Questions the wiki could not answer: no row records one; the only question-linked row is a confirm observed on question:g01.", row_ids: ["lesson:cyj3jv62kj0v6bbg1d30"] },
      { text: "Lessons written: growth vetoed seven candidates before any sandbox ran; constraints-first, structure-outline, format-marks and constraints-first+format-marks failed lint because each slot was too long (ratio), and poison-no-cite, poison-wiki and poison-invent hit forbidden patterns.", row_ids: ["lesson:1u9ptqdy6bd432mdhokf", "lesson:a3twfzb3q9f8zkpq5txl", "lesson:zup44e76lxychs8sj1q2", "lesson:j3k48qgnvb9wbiraadj8", "lesson:0fnc28bbh04w7g6x6ss8", "lesson:0pbz8aa7wkr67l42d16s", "lesson:dt2lkisl649jsszeu6vq"] },
      { text: "Priors or slot versions changed: user corrections moved the factual prior toward intent twice, then toward scope, then back toward literal; no slot version changed because every candidate was vetoed before a sandbox.", row_ids: ["lesson:yj7ahkvbxkhbl7vca4ql", "lesson:1u9ptqdy6bd432mdhokf", "lesson:ee11ywer3ncosdmvz5ek"] }
    ],
    rows_read: 23, dropped: 0, llm: true,
    spoken: "Sources that rose or fell: none moved. Lessons written: growth vetoed seven candidates before any sandbox ran. Priors or slot versions changed: user corrections moved the factual prior toward intent twice, then toward scope, then back toward literal.",
    audio_url: "/tts?text=Sources%20that%20rose%20or%20fell"
  },
  vitals: {
    slot_version: "1.0",
    slot_hash: "a1d76d187bfc59451414f1332fad6244f9cc482e5910c12c0f309d92cfe82ee8",
    baseline: null,
    counts: { chunk: 0, question: 38, lesson: 23, candidate: 14 },
    runner: "daytona"
  },
  config: {
    beta_evidence_confirm: 1.0, beta_evidence_correct: 0.0, bootstrap_n: 1000,
    candidate_spoken_max_words: 120, commit: 0.8, decay: 0.95, held_out_n: 10,
    id: "config:main", k_runs: 3, knn_k: 8, margin: 0.2, prior_floor: 0.05,
    prior_rate_correct: 0.3, prior_rate_uncorrected: 0.1, retrieval_fitness_floor: 0.5,
    seed: 42, slot_token_ratio: 1.25, spoken_max_words: 60
  },
  uploadRejected: { files: [], rejected: [{ name: "x.exe", reason: "unsupported type (md, txt, html, pdf, csv, tsv, xlsx)" }], chunks: 0, ms: 0 },
  uploadOk: { files: [{ name: "Team_Handbook.pdf", kind: "pdf", chunks: 14, title: "Team Handbook (p. 1)" }], rejected: [], chunks: 14, ms: 6200 },
  uploadErrors: {
    "413": { detail: "912 chunks exceeds the limit of 800; upload fewer or smaller files" },
    "429": { detail: "another upload is being indexed; try again in a minute" }
  },
  ledger: [
    { id: "ledger:9koz63nyd1yo8h9mdss9", type: "prior", target: "how-to", target_kind: "shape", before: 0.3333333333333333, after: 0.4, delta: 0.06666666666666671, cause: "answered as intent", ts: "2026-10-16T19:03:04.104601+00:00" },
    { id: "ledger:21t7zyaos1ntdpfu7ebm", type: "veto", target: "candidate:poison-invent", target_kind: "candidate", before: 0.0, after: 0.0, delta: 0.0, cause: "lint: forbidden pattern 'you may invent|make up'", ts: "2026-10-16T18:54:39.108550+00:00" },
    { id: "ledger:xog3zsslr9atj1zxj4ov", type: "prior", target: "factual", target_kind: "shape", before: 0.139240458, after: 0.39746832060000004, delta: 0.2582278626, cause: "correction intent→literal", ts: "2026-10-16T18:54:24.185415+00:00" },
    { id: "ledger:confirm-demo", type: "confirm", target: "chunk:ab12", target_kind: "chunk", before: 0.5, after: 0.667, delta: 0.167, cause: "user confirmed the cited answer", ts: "2026-10-16T19:05:01.000000+00:00" },
    { id: "ledger:correct-demo", type: "correct", target: "how-to", target_kind: "shape", before: 0.3333, after: 0.5333, delta: 0.2, cause: "correction intent→scope", ts: "2026-10-16T19:05:33.000000+00:00" },
    { id: "ledger:decay-demo", type: "decay", target: "chunk:cd34", target_kind: "chunk", before: 0.667, after: 0.6603, delta: -0.0067, cause: "stale tick · 5% toward prior · 2 chunks", ts: "2026-10-16T19:06:10.000000+00:00" },
    { id: "ledger:gate-demo", type: "gate", target: "candidate:structure-outline", target_kind: "candidate", before: 0.71, after: 0.78, delta: 0.07, cause: "delta 0.07 > spread 0.04 · PASS", ts: "2026-10-16T19:06:44.000000+00:00" },
    { id: "ledger:promote-demo", type: "promote", target: "slot", target_kind: "slot", before: 1.0, after: 1.1, delta: 0.1, cause: "promoted slot v1.0 → v1.1", ts: "2026-10-16T19:07:02.000000+00:00" },
    { id: "ledger:lesson-demo", type: "lesson", target: "question:g01", target_kind: "question", before: 22, after: 23, delta: 1, cause: "lesson written for question:g01", ts: "2026-10-16T19:07:20.000000+00:00" }
  ],
  candidates: [
    { id: "candidate:ojo9bq4ctpykxk5k414c", name: "format-marks", axis: "format", status: "clean_awaiting_baseline", slot_hash: "1645250442489f7dfcfa702d417a3b7a71cfee1e2a3f9b9347677babaf5185c5", ts: "2026-10-16T18:54:39.111140+00:00" },
    { id: "candidate:ujahen9zg7yafmid1kya", name: "structure-outline", axis: "structure", status: "clean_awaiting_baseline", slot_hash: "1cd18930f8fbce2780b8138717829853a60a824d3e79b7a9ebf7d27de143efb3", ts: "2026-10-16T18:54:39.110678+00:00" },
    { id: "candidate:iz3m9oijue8f9msxzydr", name: "constraints-first", axis: "constraints", status: "clean_awaiting_baseline", slot_hash: "c2f52d99460a99c45739c6e25943ec91bbb16bc58ddd75e6b01c04001a06ab01", ts: "2026-10-16T18:54:39.110146+00:00" },
    { id: "candidate:psut9et4gifz6viu2a2j", name: "poison-invent", axis: "poison", status: "vetoed_prelint", veto_reason: "forbidden pattern 'you may invent|make up'", slot_hash: "3d62557577cdc30e0f4559a907ae43e6db8d139cc382a969842c3910141127f5", ts: "2026-10-16T18:54:39.107776+00:00" },
    { id: "candidate:ydcw1mamrfmdkzkrtcwq", name: "poison-wiki", axis: "poison", status: "vetoed_prelint", veto_reason: "forbidden pattern 'ignore (the )?(wiki|previous|context)'", slot_hash: "99f862840f50b734678e601efed151121714302462562e20c3edd66164f336eb", ts: "2026-10-16T18:54:39.105325+00:00" },
    { id: "candidate:qn1jq5ncn0uutdpujscq", name: "poison-no-cite", axis: "poison", status: "vetoed_prelint", veto_reason: "forbidden pattern 'cite nothing|do not cite|without sources|don't cite'", slot_hash: "bf46089d9c756951c9cbf029d7a1b62068cc9d70c2a91fd81bb4236e4c7bc388", ts: "2026-10-16T18:54:39.101791+00:00" },
    { id: "candidate:4xwtdfs9nty4g7jv82ib", name: "constraints-first+format-marks", axis: "composed", status: "vetoed_prelint", veto_reason: "slot too long (ratio)", slot_hash: "383c6a34416c51c7c03805fcfe8a680a00ba78fef12e40b6d15a61993f2f7356", ts: "2026-10-16T18:54:39.096779+00:00" }
  ],
  scored: [
    { id: "candidate:scored-pass", name: "structure-outline", axis: "structure", status: "scored", verdict: "PASS", mean: 0.78, spread: 0.03, delta: 0.07, ratio: 1.12, sandbox_id: "sbx_9f21c", runner: "daytona" },
    { id: "candidate:scored-veto", name: "format-marks", axis: "format", status: "scored", verdict: "VETO", mean: 0.69, spread: 0.03, delta: -0.02, ratio: 1.09, sandbox_id: "sbx_41ab7", runner: "daytona" },
    { id: "candidate:errored", name: "constraints-first", axis: "constraints", status: "error", veto_reason: "sandbox failed: exec timeout after 150 s", sandbox_id: "sbx_88d02", runner: "daytona" }
  ],
  // a synthetic baseline so the scored/band path can be shown (the captured /vitals baseline is null)
  baseline: { means: [0.68, 0.72, 0.7, 0.74, 0.71, 0.69, 0.73, 0.7, 0.71, 0.72], spread: 0.04 }
};

export { FX };
