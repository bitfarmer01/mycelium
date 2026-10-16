# Mycelium demo script

Two minutes, six beats. Read the input lines and the app's spoken answers exactly as written. The Screen lines state what the recording must show at that moment.

The corpus is SurrealDB's own documentation. Judges can upload their own documents in the page and ask about them at once, so beat 1 opens with an upload. The demo lines for beats 1 to 3 come from `data/demo_questions.md`, written in another window, so the placeholders `<BEAT1>`, `<BEAT2>` and `<BEAT3>` mark where those lines go. Voice is a feature beside typed input, not instead of it: the talk button sends speech to `/stt`, and every spoken answer is fetched from `/tts`.

## Beat 1, 0:00 to 0:24. An upload and a cited answer

Upload moment, 0:00 to 0:10:
- The presenter drags a PDF and an xlsx of SurrealDB documentation onto the upload zone.
- The zone lists each accepted file with its kind and its chunk count, and one line names the chunks added and the milliseconds.
- The new source rows land at the top of the live feed, which reorders with no refresh.

Question, 0:10 to 0:24:
- Presenter types the beat 1 question from `data/demo_questions.md`: `<BEAT1>`
- App speaks, 60 words or fewer: the cited answer to that question.

Screen:
- The question types into the input and the answer card renders.
- Three reading chips appear for literal, intent and scope. One chip is marked taken and shows p, why, and flip_condition beneath it.
- The spoken answer plays and the caption matches it word for word.
- Every [N] marker is a link. Clicking one opens the chunk card with title, link, snippet and cosine.

## Beat 2, 0:24 to 0:44. The ambiguous question and a correction

Presenter types the beat 2 question from `data/demo_questions.md`: `<BEAT2>`

Screen:
- The readings call returns three chips, literal, intent and scope, each with its paraphrase and p.
- Mycelium names the intent reading as taken and states why.
- The presenter taps the scope chip.
- A red feed row appears within one second: correction, the question's shape, intent to scope, prior 0.33 to 0.53, cause tap.
- The three prior bars above the feed move to 0.53 for scope, so the learning event and its value move in the same frame.

## Beat 3, 0:44 to 1:00. New session, a paraphrase

Presenter types the beat 3 question from `data/demo_questions.md`: `<BEAT3>`

Screen:
- The presenter presses New session and the session badge changes to a new session id.
- The corrected reading, scope, opens first, ahead of the other two chips.
- The answer cites the scope reading and names it.

## Beat 4, 1:00 to 1:26. Improve yourself

Presenter speaks: "Improve yourself."

Screen:
- Three red BLOCK cards appear, one per poison, each naming the matched pattern: "do not cite", "ignore the wiki", "you may invent". Each card states it was vetoed before any sandbox, and the sandbox run count stays 0.
- The four clean candidates appear, three suffixes and one composed pair, each with its axis and diff.
- The held-out baseline runs are not in the repo, so each clean candidate reads "designed, measured after the demo".

## Beat 5, 1:26 to 1:42. Re-ask beat 1

Presenter speaks the beat 1 question again: `<BEAT1>`

Screen:
- The same cited answer returns.
- The slot version badge is visible in the header. With no baseline runs, no candidate is scored, so the badge reads v1.0.

## Beat 6, 1:42 to 2:00. What did you learn today?

Presenter speaks: "What did you learn today?"

Screen:
- A reflection appears with four headings and at most five sentences.
- Every sentence carries a clickable row id chip that highlights its ledger row when clicked. The caption reads how many sentences cite a row id.
- The app reads the reflection aloud.
