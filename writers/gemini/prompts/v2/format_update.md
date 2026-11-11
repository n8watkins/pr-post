## The midweek update

- `report_md`: the update body in Markdown, without the title line (the title is added for you).
  The whole update, title included, is at most 120 words.
  Use exactly this format:

```markdown
- 2 to 4 bullets (never more than 4), at most 25 words each (aim for 15 to 20), biggest news first.
- Each bullet: the project, then in plain words what changed and why it matters, then the item's `link:` copied exactly, like "[PR 19236](https://...)" or "[CodexBar v0.84.0](https://...)".
- The first bullet must stand alone: it is this person's line in the email.
```

  Pick from the "Top news" list first: a new repo, a release, a video or a big speedup beats any single fix.
  Several releases of one project make one bullet ("released v0.87.0 and v0.88.0").
  Always name the project a release belongs to ("CodexBar v0.84.0", never a bare "v0.84.0"); releases with no notes share one bullet ("also released CodexBar v0.84.0 and RepoBar v0.10.4").
  A new repo is news: say what it is in the words of its "New repos" line.
  Only when the brief has nothing but version bumps, chores or formatting is the body the single line `Quiet since <the window's start>.`
  If the Top news list has a release, new repo, video or archived repo, the person is not quiet.
