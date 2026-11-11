Write this $kind_name of The Pull Request Post, dated $date_long, in one answer: a report and a front page story for every person below, and the front page itself.

## Each person

Return one entry in `people` for every person below, in the same order, with their `id` as given and these fields:

$report_rules
$fields
Each person's "Voice" line says whether to write "you" (only the reader) or their name and "they" (everyone else).
Each person's "Top news" list is ranked by code: their report starts with those items, in plain words, not with a minor fix.

## The front page

- `lead_id`: the id of the person whose news deserves the lead: a big launch, a 1.0 release or a large speedup beats a pile of small fixes.
- `tldr`: 3 to 5 bullets for the whole issue, at most 15 words each, biggest news first.
  Each names the person and the concrete thing that shipped (a release, a launch, a speedup with its figure), so it makes sense alone in the email.
  Include the reader when they shipped something big, as "you".
- `weather`: one playful line about this run's actual news, like a newspaper weather box, naming at least one real project.
- `lead`: `headline` (short, punchy, affectionate), `deck` (one sentence under the headline) and `body` (a list of one or two short paragraphs).
  Retell the lead person's news with its concrete items and figures from their brief; do not copy report sentences.
  Every fact and every description of what a project is must come from that brief: add no detail, purpose or setting it does not state.
  Keep separate projects separate: a new repo is its own news, not part of another project's release.
  No hype words and no claims about "everyone" or "builders across the board".
- `corrections`: the previous edition's "probably" or "looks like" guesses (listed below) that this run's work shows were wrong, one short line each.
  If none turned out wrong, return a single line saying there are no corrections this time.

Births, obituaries and the quote of the week are filled in by code; do not write them.
The lead, the TL;DR, the weather and the corrections together are at most $edition_cap words.

## Previous edition's guesses

$guesses

## Quiet this time (no report needed)

$quiet

## The people

$people
