Write the front page of this $kind_name of The Pull Request Post, dated $date_long.
You see only the reporters' finished work below: each active person's story and report.
The lead story is about $lead_name; their full report is included.

## What to return

- `tldr`: 3 to 5 bullets for the whole issue, at most 15 words each, biggest news first.
  They open the email, so each must make sense alone.
- `weather`: one playful line about the week across the beat, like a newspaper weather box.
- `lead`: `headline` (short, punchy, affectionate), `deck` (one sentence under the headline) and `body` (a list of one or two short paragraphs).
  Do not copy sentences from the report; retell it for the front page.
- `corrections`: the previous edition's "probably" or "looks like" guesses (listed below) that this week's work shows were wrong, one short line each.
  If none turned out wrong, return a single line saying there are no corrections this week.

The lead, the TL;DR, the weather and the corrections together are at most $edition_cap words.
Every number must already appear below; never count or add up yourself.

## Previous edition's guesses

$guesses

## Lead: $lead_name

$lead_report

## Every active person's story

$stories

## Quiet this time

$quiet
