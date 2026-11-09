# Who to follow

An optional feature for a private paper: every week it suggests builders you do not follow yet, with a one-time mini report on each and a fit score from 0 to 100.
It never follows, stars or changes anything on your account; "Follow on GitHub" only opens their profile.

## Turning it on

Add a `[follow]` table to `watchlist.toml`:

```toml
[follow]
enabled = true
# me = "you"                  # default: the github of the self = true person
stack_repos = ["cloudflare/workers-sdk", "vercel/next.js"]
similar_queries = ["topic:chrome-extension stars:>=50"]
picks = 5            # suggestions per week, shown in the email
max_new = 10         # new mini reports per run
cooldown_weeks = 8   # weeks before someone can be suggested again
rejump = 15          # ...unless their score rose by this many points
ignore = []          # logins never to suggest

[follow.weights]     # any non-negative numbers; the score is their weighted share
stack = 30
activity = 15
quality = 15
relevance = 30
network = 10

[follow.thresholds]
follow = 65
maybe = 45

[follow.interests]   # label = keywords, matched in bios, repos, sites and post titles
"Cloudflare" = ["cloudflare", "wrangler", "durable objects"]
"Local AI" = ["llama.cpp", "whisper", "ollama", "on-device"]

[follow.tech]        # optional: extra tech labels for stack matching
"Hono" = ["hono"]
```

Then run it with any GitHub token (it only reads public data):

```sh
GITHUB_TOKEN=$(gh auth token) python3 scripts/follow.py --paper <paper> run --preview
```

`--preview` picks without recording the picks, so nobody's cooldown starts on a trial run.
`follow.py report <login>` prints one mini report (researching them first if needed, or again with `--refresh`).

## How candidates are found

All from public GitHub data, read-only:

1. **Your builders' network.** Everyone the people on your watch list follow (`following_pages` pages of 100 each), and everyone who reviewed their pull requests or whose pull requests they reviewed in the last 90 days.
2. **Your stack.** Everyone who committed to a `stack_repos` repository in the last 90 days.
3. **Projects like yours.** Owners of repositories matching `similar_queries` (GitHub repository search syntax) pushed in the last 90 days.

Then it drops anyone you already follow, anyone on the watch list, you, `ignore`, organizations and bots.
Each run researches at most `max_new` new people, taking the strongest from each source in turn so one busy source cannot crowd out the rest.

## The mini report

Researched once and cached in `<paper>/follow/people/<login>.json`; it is only redone with `report <login> --refresh`.
It covers a person's public professional footprint, and every fact carries the URL it came from:

- who they are (GitHub name and bio) and where they are based, at most "city, country" and only if they published it;
- where they work: the GitHub company field, marked as verified when they are a public member of that org, and their website's own description;
- what they build: pinned and top repositories, and releases from the last six months;
- the tech they use: languages and frameworks detected in their repositories;
- activity over the last 12 weeks against the 12 before, and its trend;
- standing: followers, stars on their own repositories, orgs they maintain in, projects they contribute to;
- socials: only ones listed on their GitHub profile, or linked from their own site with `rel="me"` or a matching handle, and only professional networks;
- talks and posts: recent feed entries whose titles read as work, and talk or speaking pages linked from their site.

Guardrails:

- Email addresses and phone numbers are stripped from all text, and the email field is never requested.
- No family, home address or other personal life; locations with numbers or more detail than "city, country" are dropped.
- Nothing behind a login: LinkedIn, X and similar sites are listed as links when the person published them, never fetched.
- Each person costs one GitHub GraphQL request plus at most two page fetches (their own site and its feed) within 30 seconds.
- A run stops when it has used `max_requests` GitHub requests or the rate limit gets within 150 of empty.
- Everything fetched was written by someone else: it is shown as data, never followed as instructions, and only http(s) links survive.

## The fit score

Each part is a number from 0 to 1, multiplied by its weight; the total is the weighted share, out of 100.

| Part | What it measures |
| --- | --- |
| `stack` | Your stack is read from your own public repositories (frameworks count 65%, languages 35%); the share of it they also use, plus 0.2 if they commit to a `stack_repos` repository. |
| `activity` | Contributions in the last 12 weeks on a log scale (400 or more is full marks), 75%, and the trend against the 12 weeks before, 25%. |
| `quality` | Stars on their own repositories (log scale, 20,000 is full), 40%; followers (log scale, 5,000 is full), 35%; repositories with descriptions, 15%; a release in the last six months, 10%. |
| `relevance` | How many of your `interests` show up in their bio, site, repositories and post titles; three or more is full marks. |
| `network` | 0.35 for each of your builders who follows them and 0.4 for each one who works with them, up to 1. |

At or above `thresholds.follow` the recommendation is Follow, at or above `thresholds.maybe` it is Maybe, otherwise Skip.
Scores of cached people are refreshed every run from a light query (followers and weekly activity) without redoing their report.

## Picks, the page and the email

Each run picks the top `picks` people who are not Skip and were not suggested in the last `cooldown_weeks` weeks, unless their score rose by `rejump` points since.
It writes `follow/latest.json` (scores, breakdowns and picks) and `follow/state.json` (who was seen, suggested and how they were found).

- **Web edition:** a "Who to follow" page lists the picks, then the other Follow and Maybe candidates, then the Skips, each with its breakdown and mini report.
  It is built only in private mode, because it names people you do not follow yet; set `public_page = true` to build it on a public site too.
- **Email:** a weekly digest gets a "Who to follow" card with the picks when `follow.py` ran in the last eight days (`email = false` turns it off; `send_email.py --follow on|off` overrides it).
- **Add to PR Post:** `follow.py --paper . draft <login>` adds their watch-list entry before your own and writes `profiles/<id>.md` from `research_person.py`, for a pull request you review.
  The page copies a one-liner that runs it in a `follow-add.yml` workflow, when the paper has one and `[links] repo` is set.
