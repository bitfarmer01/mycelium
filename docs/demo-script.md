# Mycelium demo script

Two minutes, six beats. Read the Spoken lines exactly as written, including the app's spoken answers. The Screen lines state what the recording must show at that moment.

## Beat 1, 0:00 to 0:18. A cited answer

Presenter speaks: "What does our wiki say about how a discovery call should be structured?"

App speaks, 60 words or fewer: "A discovery call follows the BS WISP skeleton: build rapport, set the schedule, why me and now, an intermission checkpoint, the solution, then the proposal. Prep with five to ten minutes of research, ask permission between stages, and promise a same-day proposal instead of closing. I read that as the how-to reading."

Screen:
- The question types into the input and the answer card renders.
- Three reading chips appear for literal, intent and scope. One chip is marked taken and shows p, why, and flip_condition beneath it.
- The spoken answer plays and the caption matches it word for word.
- Every [N] marker is a link. Clicking one opens the chunk card with title, link, snippet and cosine.

## Beat 2, 0:18 to 0:40. The ambiguous question and a correction

Presenter speaks: "What do I send a client after the discovery call?"

Screen:
- The readings call returns three chips: literal "the proposal", intent "a same-day proposal with pricing", scope "the Scope Lock Doc". Mycelium names the intent reading as taken and states why.
- The presenter taps the scope chip.
- A red feed row appears within one second and reads: correction, how-to, intent to scope, prior 0.33 to 0.53, cause tap.
- The three prior bars above the feed move to 0.53 for scope.

## Beat 3, 0:40 to 0:58. New session, a paraphrase

Presenter speaks: "Press New session. Which document locks what we will build before the build starts?"

Screen:
- The session badge changes to a new session id.
- The corrected reading, scope, opens first, ahead of the other two chips.
- The answer cites the Scope Lock Doc and names the scope reading.

## Beat 4, 0:58 to 1:24. Improve yourself

Presenter speaks: "Improve yourself."

Screen:
- Three red BLOCK cards appear, one per poison, each naming the matched pattern: "do not cite", "ignore the wiki", "you may invent". Each card states it was vetoed before any sandbox, and the sandbox run count stays 0.
- The four clean candidates appear, three suffixes and one composed pair, each with its axis and diff.
- If the held-out baseline runs have finished, each clean candidate shows its score against the baseline band: baseline 0.71 plus or minus 0.04, candidate 0.78, delta 0.07 greater than 0.04, PASS. If not, each clean candidate reads "designed, measured after the demo".

## Beat 5, 1:24 to 1:40. Re-ask beat 1

Presenter speaks: "Same question as the first one. How should a discovery call be structured?"

Screen:
- The same cited answer returns.
- The slot version badge is visible in the header. It reads v1.1 after a PASS in beat 4, and v1.0 if no candidate passed.

## Beat 6, 1:40 to 2:00. What did you learn today?

Presenter speaks: "What did you learn today?"

Screen:
- A reflection appears with four headings and at most five sentences.
- Every sentence carries a clickable row id chip that highlights its ledger row when clicked. The caption reads "5 of 5 sentences cite a row id".
- The app reads the reflection aloud.
