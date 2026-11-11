## The weekly report

- `report_md`: the report body in Markdown, without the title line (the title is added for you).
  The whole report, title included, is at most 250 words.
  Use exactly this format:

```markdown
## TL;DR
- 2 to 4 bullets, at most 20 words each, the biggest first.
- The first bullet is this person's line in the email, so it must stand alone: the project and what shipped.

## <Project> (at most 3 sections, busiest first)
- 1 to 3 bullets in plain English: what changed and why it matters, then a Markdown link to the PR, commit, release or video from the brief.
- Group related PRs into one bullet ("several PRs fixing backup and restore") instead of listing them.
- Say what the project is in a few words only when the profile does not mention it yet, and only in the words of its "New repos" or README line.

## New, renamed or shut down
New repos, archived repos and pivots, one line each; leave the section out if empty.

## Worth asking them about
One genuinely interesting question this week's work raises; leave it out if nothing stands out.
```

  Pick from the "Top news" list first: new repos, releases, videos and big speedups lead.
  Only when the brief has nothing but version bumps, chores or formatting is the body the single line `Quiet since <the window's start>.`

  For the person marked as the reader (and nobody else), write in the second person ("you shipped...") and add one more section at the end (it counts toward the 250 words):

```markdown
## How you look from the outside
What a stranger landing on their GitHub this week would conclude, in 2 or 3 bullets.
Point at concrete things they can fix: repos with no description or README, a missing bio, work that is invisible because it is private, stale pinned repos, commit messages or PR titles that read badly.
Say what looks strong too.
```
