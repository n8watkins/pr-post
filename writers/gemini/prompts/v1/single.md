Write this $kind_name of The Pull Request Post, dated $date_long, in one answer: a report and a front page story for every person below, and the front page itself.

## Each person

Return one entry in `people` for every person below, in the same order, with their `id` as given and these fields:

$report_rules
$fields
## The front page

- `lead_id`: the id of the person whose news deserves the lead.
- `tldr`: 3 to 5 bullets for the whole issue, at most 15 words each, biggest news first.
  They open the email, so each must make sense alone.
- `weather`: one playful line about the week across the beat, like a newspaper weather box.
- `lead`: `headline` (short, punchy, affectionate), `deck` (one sentence under the headline) and `body` (a list of one or two short paragraphs).
  Do not copy sentences from the report; retell it for the front page.
- `corrections`: the previous edition's "probably" or "looks like" guesses (listed below) that this week's work shows were wrong, one short line each.
  If none turned out wrong, return a single line saying there are no corrections this week.

The lead, the TL;DR, the weather and the corrections together are at most $edition_cap words.

## Previous edition's guesses

$guesses

## Quiet this time (no report needed)

$quiet

## The people

$people
