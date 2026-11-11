Write the $kind_name report on one person, and their front page story.

$report_rules
$fields
## The person

$person

## Before you answer, check

- Links: copy each link exactly as an item's `link:` gives it, short label included ("[PR 15162](...)").
  No other address: never build one yourself, and never link a repo's own page unless an item's `link:` is that page.
- Words: every bullet has at most $bullet_words words (count them; aim for 15 to 20), the story summary at most 30, and the whole report at most $cap.
- Link labels have at most $label_words words.
- Voice: follow the Voice line above exactly.
- Shape: one JSON object with exactly these keys, like this (values shortened):
  `{"report_md": "- ...\n- ...", "story": {"beat": "Tools desk", "headline": "...", "summary": "..."}, "profile_lines": [], "quote_candidate_ids": ["i4"]}`
  (plus `profile_md` only when asked for above).
