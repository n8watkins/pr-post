- `story`: their entry on the front page: `beat` (a desk name such as "Agency desk" or "Tools desk"), `headline` (short and punchy, naming the project) and `summary` (at most 30 words, not copied from the report, in the same voice as the report).
- `profile_lines`: one short line for each new project, retired project or change of direction that their profile does not already record; an empty list when nothing changed (the usual case).
  Write only the text, with no date and no leading "-": the date is added for you.
- `profile_md`: only when asked for below, because they have no profile yet: a profile written from the brief in at most 400 words, with a `# <name>` title, two or three sentences on who they are, and a `## Projects` list with a few words and a link per project.
- `lead_score`: 0 to 10, how much this person's news deserves the front page lead (10: a big launch, a 1.0 release or a large speedup; 0: routine upkeep).
- `quote_candidate_ids`: up to 3 item ids (like "i4") whose title would make a good, fun "quote of the week": a title with some voice, not a conventional-commit title such as "fix(ci): ...".
  The quote is copied exactly from the data by code, so pick ids, never text.
