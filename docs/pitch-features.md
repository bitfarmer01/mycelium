# Mycelium: what we pitch

Decided 2026-10-16 after live calls against our own sponsor accounts. Full inventory, evidence and re-ranking: `sponsor-capabilities.md`; request log: `sponsor-capabilities.spike-log.md`; re-check with `verify.sh` (all three in the spike scratchpad, see "Where the evidence lives").

## North star

A voice-first company knowledge organism that gets better every time someone talks to it. It answers from the wiki with citations, learns from corrections, ranks what it knows by proven usefulness, and rewrites its own prompt only after sandboxed candidates beat the baseline on held-out questions.

A feature made the cut only if it shows that loop on screen, and every platform call it depends on either answered live or has a stated reason why it can't yet. We pitch #1–#4. #5 is a stretch slide only, because nothing in `product-goal.md` promises learning skills from a teammate.

## The four, plus one stretch

| # | Feature | Sponsor | Demo beat | What the judge sees | Live status |
|---|---|---|---|---|---|
| 1 | **Exam burst** | Daytona | 4: "improve yourself" | The two candidate prompts and the poison slot each run in their own sandbox at once, Claude Code inside each; scores land, one wins, the poison is vetoed | Verified: 3 sandboxes, 4–7 s for the whole burst |
| 2 | **Every answer traced for free** | Arize AX | 1 and 4 | Any answer or exam run opened in Arize as a turn with its LLM call, model, tokens and cost, with no instrumentation code | Verified under headless `claude --print`; spans arrive within about 10 s |
| 3 | **Heartbeat feed** | SurrealDB | 2, 3, 5 | The usefulness leaderboard and correction feed move the instant a correction lands, with no refresh | Verified: LIVE SELECT pushes in about 2 ms (local server) |
| 4 | **Inner voice** | ElevenLabs | 6: "what did you learn today?" | Answers in one voice; reflects on what it learned in a second, quieter one | Agent config verified (two labelled voices plus expressive mode); audible switching unproven until the custom-LLM endpoint exists |
| 5 | **Show-me learning** (stretch slide, not pitched) | Anchor | growth (not in the product goal) | It can't find something, sends a teammate a link, she clicks through once, and it has a new skill | Demonstration link verified on our Free plan; compile time unproven until one human demonstration is run |

**Sixth, once unblocked: The diff it just learned** (SurrealDB Agent Memory, beat 2). This is the on-screen "I believed London, now Berlin" receipt. It needs an Agent Memory context and key we don't have yet (see "Open items"). Until then, beat 2 runs on plain SurrealDB observations, which the Heartbeat feed already shows.

## What we dropped and why

- **Evolution by fork, drawn as a family tree** (Daytona) was the docs' strongest idea. Our org can't create the VM sandboxes that fork needs: they aren't available in our region, and Windows needs Tier 3. The claim it carried, that sandboxed candidates beat the baseline, moves to Exam burst unchanged. Fork comes back if Daytona enables VMs for our org.
- The other Agent Memory features (Belief chain, Reflects aloud, Why this answer and the rest) wait on the same missing context as the sixth feature.

## Constraints the build must respect

- **Daytona pool: 10 GiB of memory in total.** That fits two `daytona-medium` sandboxes or three `daytona-small`, and three is exactly the burst we need. Each sandbox is capped at 4 vCPU / 8 GB / 10 GB. Set auto-stop to 0 for exam runs (the default is 15 min).
- **Claude Code inside sandboxes authenticates through Bedrock.** It uses the AWS workshop account (Opus 4.5), not the Max plan the spec's hard rule names. Say so in the pitch. The workshop account ends around **2026-10-19 09:44 EDT**, and its session credentials can lapse sooner: refresh `.env` if Bedrock checks start failing.
- **ElevenLabs:** set `expressive_mode: true` explicitly, because it's off after a model switch. An English agent's base model must be flash v2 or turbo. The custom-LLM proxy over `claude --print` (about 3h) is still required plumbing for Inner voice.
- **Anchor:** pass session timeouts when creating a demonstration. The defaults are 20 min, and only 5 min idle. Delete the task record each demonstration creates.
- **Arize:** the space ID can be found from the API key, so `ARIZE_SPACE_ID` is optional. Every `claude --print` session pays a ~45k-token cache write, which counts in exam cost.

## Open items

1. **Agent Memory:** an Owner subscribes on the SurrealDB Billing page and deploys a context in Studio. Then add `SURREAL_AGENT_MEMORY_URL`, `SURREAL_AGENT_MEMORY_CONTEXT` and an `sp-` key to `.env`. The London → Berlin check is already written and runs with `run.py spike surreal`.
2. **Audible voice switching:** prove it in the first live call through the custom-LLM endpoint.
3. **Anchor compile time:** run one real demonstration by hand and time it before deciding whether Show-me learning is live or staged.
4. **Max-plan auth in a sandbox:** untested; Bedrock is the working route.
5. **Daytona VMs:** ask Daytona support to enable linux-vm if we want the family tree back.

## Cut order if time runs short

Show-me learning is already out of the pitch. First to cut is the second voice in Inner voice, falling back to one voice reading the reflection. Exam burst, tracing and the Heartbeat feed are the spine and stay.

## Where the evidence lives

These files are in the spike scratchpad, `/private/tmp/claude-501/-Users-rajathraghu-Developer-kb-core/783c09ca-dce5-46f8-b90a-bf532d459dea/scratchpad/`. That is a temporary directory: copy them into this repo before it is cleared.
- `sponsor-capabilities.md`
- `sponsor-capabilities.spike-log.md`
- `verify.sh`
- `spike/`
