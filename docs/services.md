# Mycelium: sponsor services and what they can do

Reference for the services behind the keys in `.env`. Pulled from each vendor's
docs on 2026-10-16. Capabilities are the general platform features; "In Mycelium"
notes tie back to the features in `pitch-features.md`.

Do not paste live key values into this file. Keep secrets in `.env` (git-ignored).

---

## 1. Anchor Browser — `ANCHOR_API_KEY` (`sk-...`)

Cloud browsers for AI agents. Site: https://anchorbrowser.io — Docs: https://docs.anchorbrowser.io

- **Natural-language web tasks** — pass a prompt ("go to X and extract Y"); the agent navigates, clicks and returns data via `agent.task`.
- **Cloud browser sessions over CDP or REST** — attach Playwright/Puppeteer; full control of a real headless browser.
- **Human demonstration → learned skill (Replicate)** — demonstrate a workflow once; the agent records the action sequence and replays it reliably. This is the basis of Show-me learning.
- **Managed authentication** — applications, identities, MFA, OmniConnect for persistent logins across sessions.
- **Versioned automation tasks** — deploy TypeScript workflows once, run them on demand.
- **Anti-bot toolkit** — residential proxies, dedicated sticky IPs, VPN routing, extra-stealth fingerprints, captcha solving.
- **Also** — persistent volumes/profiles, recordings, live view, batch sessions, webhooks, MCP integration.
- **Interfaces** — Node.js + Python SDKs, REST API, Make / LangChain / CrewAI.

**In Mycelium:** feature 5 "Show-me learning" (stretch). Demonstration link verified on the Free plan; compile time unproven until one real demonstration is run by hand.

---

## 2. SurrealDB — `SURREAL_DB_ACCESS_TOKEN` (`sdbp_...`)

Multi-model database in Rust plus an Agent Memory layer. Site: https://surrealdb.com — Docs: https://surrealdb.com/docs

- **One engine, many models** — document, graph, vector (HNSW), full-text (BM25), time-series, geospatial, relational and key-value, all inside one ACID transaction.
- **SurrealQL** — SQL-shaped query language with graph traversal and nested access; also GraphQL, a REST API, and custom `DEFINE API` HTTP endpoints.
- **Live queries** — subscribe to a filtered set of records and receive changes as they happen.
- **Table events / async events** — fire on create, update and delete; async events run after commit.
- **Hybrid retrieval** — vector similarity + full-text + graph traversal, fused with reciprocal rank fusion (RAG / Graph RAG).
- **Agent Memory** — typed memory (episodes, identity, knowledge, context, instructions, uncertainty), a knowledge graph, hybrid recall, provenance and time tracking (superseded beliefs are end-dated, not overwritten), autonomous reflection/consolidation.
- **Deployments** — embedded, single-node, distributed cluster, or managed Cloud. Namespace/database/record-level access control; MCP server for coding agents.

**In Mycelium:** feature 3 "Heartbeat feed" (LIVE SELECT pushes in ~2 ms locally). The sixth feature ("The diff it just learned") needs an Agent Memory context and `sp-` key we do not have yet.

---

## 3. AWS (Amazon Bedrock) — `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`

Temporary session credentials (region `us-west-2`) scoped to Amazon Bedrock (workshop account). Docs: https://docs.aws.amazon.com/bedrock

- **Managed access to 100+ foundation models** — Anthropic (Claude), Amazon Nova, OpenAI, DeepSeek, Moonshot (Kimi), MiniMax, xAI (Grok).
- **Multiple APIs** — Messages, Responses, Chat Completions, Converse and Invoke; `bedrock-runtime` endpoint recommended.
- **Model customization** — fine-tuning, continued pre-training, distillation.

**In Mycelium:** Claude Code inside Daytona sandboxes authenticates through Bedrock on Opus 4.5 (not the Max plan). The workshop account ends around **2026-10-19 09:44 EDT**, and session credentials can lapse sooner — refresh `.env` if Bedrock checks start failing.

---

## 4. Daytona — `DAYTONA_API_KEY` (`dtn_...`)

Secure, elastic infrastructure for running AI-generated code. Site: https://www.daytona.io — Docs: https://www.daytona.io/docs/en

- **Full composable sandboxes** — complete isolation with a dedicated kernel, filesystem, network stack and allocated vCPU/RAM/disk; spin up in under 90 ms.
- **Language support** — Python, TypeScript and JavaScript on OCI/Docker-compatible images.
- **Persistence** — stateful snapshots and volumes shared across sandboxes.
- **Agent tools** — file system operations, git operations, Language Server Protocol, process and code execution, PTY, log streaming, MCP server, computer use, agent skills.
- **Human tools** — web terminal, SSH, VNC, preview URLs, playground.
- **Ops & security** — warm pools, declarative builder, regions, secrets, audit logs, webhooks, OpenTelemetry collection.

**In Mycelium:** feature 1 "Exam burst" (3 parallel sandboxes, 4–7 s per burst). Constraint: 10 GiB total — two `daytona-medium` or three `daytona-small`; set auto-stop to 0 for exam runs. VMs are unavailable in our region, so "Evolution by fork" was dropped.

---

## 5. Arize AX — `ARIZE_API_KEY` (`ak-...`)

AI engineering and observability platform. Site: https://arize.com — Docs: https://docs.arize.com

- **Instrument & trace** — capture every run (inputs, outputs, tools, model, tokens, cost); auto-instrumentation for 30+ providers/frameworks; OpenTelemetry.
- **Signal (observe)** — automatically clusters recurring production failures into ranked issues with trace evidence; dashboards and alerts.
- **Alyx** — built-in AI agent you query in plain language about your traces and failures.
- **Evaluate** — LLM-as-a-judge, agent-as-a-judge, or code evaluators; human annotation and judge calibration.
- **Experiments** — run a change (prompt/model/retrieval) against a dataset and prove improvement before shipping; datasets and remote agent experiments.

**In Mycelium:** feature 2 "Every answer traced for free" (spans arrive ~10 s after headless `claude --print`, no instrumentation code). `ARIZE_SPACE_ID` is optional — the space ID can be derived from the API key. Each `claude --print` session pays a ~45k-token cache write that counts in exam cost.

---

## 6. ElevenLabs — `ELEVENLABS_API_KEY` (`sk_...`)

AI voice infrastructure. Site: https://elevenlabs.io — Docs: https://elevenlabs.io/docs

- **Text-to-speech** — lifelike voices; `eleven_v3`/`v4` expressive, `flash v2.5` / `turbo` for real time (~75–100 ms median).
- **Speech-to-text (Scribe v2)** — 90+ languages, speaker diarization up to 32 speakers, word-level timestamps, entity detection; realtime and medical variants.
- **Conversational agents (ElevenAgents)** — design and operate voice agents via visual builder or full programmatic control.
- **Voices** — 10,000+ library, clone from a recording, or design from a text description; voice changer, isolator and remixing.
- **Generative audio** — music, sound effects, dubbing, text-to-dialogue, image/video, forced alignment.
- **Interfaces** — Python and TypeScript SDKs; private (self-cloud) deployment. Billing in credits (TTS = 1 credit per character).

**In Mycelium:** feature 4 "Inner voice" — two labelled voices plus expressive mode. Constraint: set `expressive_mode: true` explicitly (it resets after a model switch) and use flash v2 or turbo for an English agent. The custom-LLM proxy over `claude --print` (~3 h) is still required plumbing; audible switching is unproven until then.

---

## Open items (from `pitch-features.md`)

- **SurrealDB Agent Memory** — an Owner subscribes on the Billing page and deploys a context in Studio, then add `SURREAL_AGENT_MEMORY_URL`, `SURREAL_AGENT_MEMORY_CONTEXT` and an `sp-` key to `.env`.
- **ElevenLabs** — prove audible voice switching through the custom-LLM endpoint.
- **Anchor** — run one real demonstration by hand and time it.
- **Daytona** — ask support to enable linux VMs if the family tree is wanted back.

## Credential hygiene

- `.env` is git-ignored, but it holds live secrets — do not paste values into chats, issues or screenshots.
- The AWS session credentials are short-lived and expire around 2026-10-19; rotate/refresh as needed.
