# Test it yourself: Mycelium judge pack

Five sample files sit in `data/judge-samples/`. They are not pre-loaded, so Mycelium knows nothing about them until you upload. Everything below is about "Northwind Robotics (sample data)", a fictional company invented for this pack.

## How to upload

Drag your files onto the drop zone on the page, or send them to `POST /upload` as a multipart form. The box then reports chunks per file. The limits, in plain words: at most 10 files in one drop, each file 20 MB or less, the whole drop 50 MB or less, and the drop must make 800 chunks or fewer. One upload runs at a time; a second one waits.

Accepted types are md, txt, html, pdf, csv, tsv, xlsx. Two rules matter for this pack. A PDF needs a text layer, so a scan of an image is skipped. A spreadsheet takes its first row as the header.

Upload all five files at once.

## Eight questions, each answerable from one file

| # | Question | File | Expected answer |
|---|---|---|---|
| 1 | What is the home office budget for a new hire? | handbook.pdf | 2000 US dollars |
| 2 | What is Focus Friday? | handbook.pdf | The last Friday of every month, with no meetings booked |
| 3 | How many weeks of parental leave do full time employees get? | handbook.pdf | 16 weeks |
| 4 | What does the Navigator plan cost per month? | pricing.xlsx | 99 US dollars |
| 5 | How many seats does the Voyager plan include? | pricing.xlsx | 25 seats |
| 6 | Which incident was severity 1, and who owned it? | incidents.csv | INC-2041, owned by Priya Nair |
| 7 | If the primary on call does not acknowledge a page, how long before it escalates? | oncall.html | 10 minutes, then the secondary on call is paged |
| 8 | Within how many hours must a suspected vulnerability be reported? | security-policy.md | 24 hours |

## One ambiguous question and the chip to tap

Ask: "What is the Voyager limit?"

Three reading chips come back. The word "limit" is ambiguous, so Mycelium usually takes the literal reading, the price 499. When that happens, tap the scope chip to correct it. The chips map like this: literal is the monthly price, intent is the seat count (25 seats), scope is the API call limit (500000 calls per month).

What moves on screen when you tap scope: the scope chip becomes the taken chip and its posterior goes to 1.00, the literal and intent chips lose their taken and posterior marks, the three prior bars shift so scope leads, and a red correction row appears in the feed naming literal to scope with the prior change and cause tap. The spoken answer text stays as it was; only the reading state moves. Ask another question of the same shape and the correction carries as a prior.

## The clash question

Ask: "How long does Northwind keep security audit logs?"

Two files disagree, on purpose, with different dates. `security-policy.md`, revision 2, effective 2025-09-15, says 180 days. `handbook.pdf`, revision 3, effective 2026-04-01, says 400 days.

Expected: Mycelium prefers the newer rule and answers 400 days, and it names both files, so you can see that the 2025 policy figure is superseded. If an answer states only one file, or picks 180 days, ask it to name its sources.

## What did you learn today?

Type "What did you learn today?" Mycelium reflects on the session so far, with a row id chip on each sentence that highlights the matching ledger row when you click it.
