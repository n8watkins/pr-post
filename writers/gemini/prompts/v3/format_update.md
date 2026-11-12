## The midweek update

- `report_md`: the update body in Markdown, without the title line (the title is added for you).
  The whole update, title included, is at most 120 words.
  Use exactly this format:

```markdown
- 2 to 4 bullets (never more than 4), at most 25 words each (aim for 15 to 20), biggest news first.
- Each bullet is one sentence about one project, ending in a period, then the item's `link:` copied exactly, like "[PR 19236](https://...)".
- The first bullet must stand alone: it is this person's line in the email.
```

  Every bullet is a full sentence whose subject is the person or the project, with a verb: "Ada added Windows support to acme, the tool that sets up build machines over SSH."
  Never "acme: added Windows support" and never "In acme, added...".
  The link comes after the sentence, never inside it: not "alongside [acme]" or "through [PR 7]".
  Start with the "Must cover" items, one bullet each, then pick from the other top news: a new repo, a release, a video or a big speedup beats any single fix.
  One project per bullet (only releases with no notes may share one): a new repo always gets its own bullet, never "plus a new repo" or "alongside" inside another project's bullet.
  Several releases of one project make one bullet ("released v0.87.0 and v0.88.0").
  Always name the project a release belongs to ("acme v0.84.0", never a bare "v0.84.0"); releases with no notes share one bullet ("also released acme v0.84.0 and widget v0.10.4").
  A new repo is news: say what it is in the words of its "New repos" line.
  A new repo and its first release are one bullet, not two.
  A video is news: say what it is about in the words of its title.

  Explain, do not reword.
  A PR or commit title is written for the people inside the project ("fix: reset per-task watch markers at idle-session boundaries").
  Rewording it ("reset per-task markers at idle boundaries") tells a friend nothing.
  Read the item's description under it, which often says why, and write what a person using the project notices: what now works, what no longer breaks, what got faster.
  A title that is already plain English ("halve report time") can stay close to its words: then add what the project is, from its line.
  If neither the title nor its description lets you say that in everyday words, pick another item.
  Never fill the gap with a benefit the brief does not state ("so it runs faster", "making reviews easier").

  Only when the brief has nothing but version bumps, chores or formatting is the body the single line `Quiet since <the window's start>.`
  If the Top news list has a release, new repo, video or archived repo, the person is not quiet.
