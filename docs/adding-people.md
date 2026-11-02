# Adding people

Following someone takes one `[[people]]` block in `watchlist.toml`.
Getting that block right, and knowing where else they publish, takes a little research.
This is the intake procedure: a script does the mechanical part, and a person (or an agent with web search) does the rest.

## 1. Draft with the script

```sh
GITHUB_TOKEN=$(gh auth token) python3 scripts/research_person.py <github-username> \
  [--org <org> ...] [--youtube UC...] --paper <your paper> --out draft.md
```

It reads, and never writes anything but `--out`:

| Source | What it records |
| --- | --- |
| GitHub profile | Name, bio, company, location, website field, X field, social accounts |
| GitHub repos | Pinned repos (needs a token), most-starred and recently pushed repos |
| GitHub activity | Public org memberships, plus outside repos they recently pushed to or opened pull requests in, as candidates for `orgs` |
| Their website | Links to known networks, `rel="me"` links and RSS or Atom feeds |
| YouTube | The channel title for `--youtube`, or the channel id behind a `youtube.com/@handle` link of theirs |
| Every link | Fetched once: "resolves", "broken", or "open it by hand" for X, LinkedIn and other sites that refuse automated checks |

`--paper` keeps the suggested ticker clear of symbols already taken.
`--json` prints the raw findings instead of the draft.
The token is read from `GITHUB_TOKEN` and never printed; without one, pinned repos are skipped and the rate limit is 60 requests an hour.

The draft has three parts: a profile in the same shape the writer uses, a "Still to check" list, and a commented-out `[[people]]` block.

## 2. Judge what it found

The script is cautious, not clever, so a person decides:

- **Ownership.** A link someone put on their own GitHub profile is theirs.
  A link found on their website counts as theirs only when the account name matches their login, name or X handle; anything else is listed separately as probably an employer's or a project's account.
  A personal site that is really a company page (the GitHub website field often is) links the company's socials, not theirs.
- **Orgs.** An org they push to every week belongs in `orgs`; a repo where they once opened a pull request does not.
  Their public org memberships are often stale.
- **Name and ticker.** Use the name you call them; tickers are up to six letters or digits and work best as a project nickname.

## 3. Search the web for the rest

For each item under "Still to check":

- **LinkedIn**: search their full name with their company or best-known project.
  Record the address only when their own site, GitHub or a page they control links to it, or a conference or publisher page clearly about them does; say which.
- **X, Bluesky, Mastodon**: a Bluesky handle that is their own domain proves itself.
  Otherwise look for a link back to their GitHub or website, or a bio that names their projects.
- **YouTube**: open the channel and confirm it is them; then take the `UC...` id from the page source (`"externalId"`) or a `youtube.com/channel/UC...` link.
  Handles are easy to squat, so a handle that matches their X name is not enough.
- **Newsletter, blog, podcast**: check the feed loads and note the date of the newest post, so an abandoned blog is not presented as active.

Mark anything you could not confirm as **unconfirmed** in the profile, and leave it out of `links`.

## 4. Write it down

- `watchlist.toml`: the `[[people]]` block.
  Only put links in `links` that are not on their GitHub profile, because those are collected automatically.
  Add `youtube_channel_id` when they post videos.
- `profiles/<id>.md` (papers with a writer): the profile, with a "Where to find them" section that says where each link came from and how it was verified, a one-line "why I follow them", and their active projects.
  The writer keeps the projects section up to date from then on.

## 5. Check it

```sh
python3 scripts/build_site.py --paper <your paper> --out dist   # fails loudly on an invalid entry
python3 scripts/feed.py --check dist/feed.json
```

To see real data for the new person without touching committed data, copy the paper and run `collect.py --paper <copy> --only <id> --out <copy>/data/<id>.json`, then `stats.py` and `build_site.py` on the copy.
Someone whose last public activity is older than the collection window shows up with an empty week; that is expected, not a bug.
