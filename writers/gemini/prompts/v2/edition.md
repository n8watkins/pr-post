Write the front page of this $kind_name of The Pull Request Post, dated $date_long.
You see only the reporters' finished work below: each active person's first report line and story.
The lead story is about $lead_name; their full report is included.
The reader is $reader_name: when you mention them, write "you" ("you shipped..."); everyone else is named in the third person.
Births, obituaries and the quote of the week are filled in by code; do not write them.

## What to return

- `tldr`: 3 to 5 bullets for the whole issue, at most 15 words each, biggest news first.
  Each names the person and the concrete thing that shipped (a release, a launch, a speedup with its figure), so it makes sense alone in the email.
  Include the reader when they shipped something big.
- `weather`: one playful line about this run's actual news, like a newspaper weather box, naming at least one real project.
- `lead`: `headline` (short, punchy, affectionate), `deck` (one sentence under the headline) and `body` (a list of one or two short paragraphs).
  Retell the lead report for the front page with its concrete items and any figures it gives; do not copy its sentences.
  Every fact and every description of what a project is must already be in the lead report: add no detail, purpose or setting it does not state.
  No hype words and no claims about "everyone" or "builders across the board".
- `corrections`: the previous edition's "probably" or "looks like" guesses (listed below) that this run's work shows were wrong, one short line each.
  If none turned out wrong, return a single line saying there are no corrections this time.

The lead, the TL;DR, the weather and the corrections together are at most $edition_cap words.
Every number must already appear below; never count or add up yourself.

## Previous edition's guesses

$guesses

## Lead: $lead_name

$lead_report

## Each active person's first report line

$lines

## Each active person's story

$stories

## Quiet this time

$quiet
