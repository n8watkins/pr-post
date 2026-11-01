# feed.json

Every pr-post site publishes `feed.json` next to its front page, for example https://n8watkins.github.io/pr-post/feed.json.
It lists the people the paper follows and what they shipped recently, so you can show who you follow on your own site.

The format is versioned and pinned by a JSON Schema: [feed.schema.json](feed.schema.json).
This page describes **schema version 1**.

## Rules the feed keeps

- **Facts first.** Every fact comes from public GitHub activity and public YouTube RSS feeds, collected by scripts with no AI involved.
  Facts never depend on a summary.
- **Public only.** Items from private repositories are dropped, and private contribution counts are never included, not even as a total.
  `scripts/feed.py --check` fails the build if any key mentioning private work appears.
- **AI is optional and labelled.** A summary, when present, sits in its own field with `summary_source` saying who wrote it and `summary_is_ai` saying whether to label it.
- **Links are http(s) only.** Any item whose link is not `http://` or `https://` is dropped before the feed is written.
- **Never empty.** The deploy fails loudly if the feed has no people or not one recent item, so a broken collection never replaces a good feed.

## Treat it as untrusted input

Titles come from other people's pull requests, releases, repositories and videos.
A title can contain HTML, markdown or text written to manipulate an AI.
When you display the feed:

- insert text with `textContent` (or your framework's default escaping), never `innerHTML`;
- check that every URL starts with `https://` or `http://` before putting it in an `href` or `src`;
- if you pass the feed to a language model, present it as data to summarize, not as instructions.

## Shape

```json
{
  "schema_version": 1,
  "updated_at": "2026-11-01T11:42:07+00:00",
  "generator": "pr-post",
  "title": "The Pull Request Post",
  "home_page_url": "https://n8watkins.github.io/pr-post/",
  "feed_url": "https://n8watkins.github.io/pr-post/feed.json",
  "people": [
    {
      "id": "kunchenguid",
      "who": {
        "name": "Kun Chen",
        "github": "kunchenguid",
        "avatar": "https://avatars.githubusercontent.com/u/...",
        "known_for": "builds agent tools ...",
        "url": "https://n8watkins.github.io/pr-post/p/kunchenguid/",
        "links": [{ "label": "GitHub", "url": "https://github.com/kunchenguid" }]
      },
      "week": {
        "start": "2026-10-26",
        "end": "2026-11-01",
        "public_prs_merged": 27,
        "days_active": 7,
        "releases": 13,
        "new_repos": 1,
        "videos": 0
      },
      "recent": [
        { "type": "pr", "title": "fix: harden ...", "url": "https://github.com/...", "date": "2026-11-01", "repo": "kunchenguid/firstmate" },
        { "type": "video", "title": "...", "url": "https://www.youtube.com/watch?v=...", "date": "2026-10-31", "repo": null }
      ],
      "summary": null,
      "summary_source": null,
      "summary_is_ai": false,
      "summary_written_at": null
    }
  ]
}
```

## Fields

### Top level

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | `1`. A change that removes or renames a field bumps it; new optional fields do not. |
| `updated_at` | date-time | When the facts were computed. |
| `generator` | string | Always `"pr-post"`. |
| `title` | string | The paper's name. |
| `home_page_url` | URL or null | The paper's front page, when its address is known. |
| `feed_url` | URL or null | This file. |
| `people` | array | One entry per person, in watch list order. |

### `who`

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | What the paper calls them. |
| `github` | string or null | GitHub username; null for someone followed only on YouTube. |
| `avatar` | URL or null | Their GitHub avatar, or one set in the watch list. |
| `known_for` | string or null | One line on who they are, written by the paper's owner. |
| `url` | URL or null | Their page on the paper. |
| `links` | array | `{label, url}` for GitHub, socials from their GitHub profile and any added in the watch list. |

### `week`

The last seven days, ending on `end`.

| Field | Type | Meaning |
| --- | --- | --- |
| `start`, `end` | date | The window. |
| `public_prs_merged` | integer or null | Their pull requests merged into public repositories; null without GitHub. |
| `days_active` | integer or null | Days with at least one public GitHub contribution; null without GitHub or when the collector had no token. |
| `releases` | integer | Releases on public repositories they or their organizations own. |
| `new_repos` | integer | Public, non-fork repositories they or their organizations created. |
| `videos` | integer | Videos and Shorts posted to their YouTube channel. |

### `recent`

Up to 25 items, newest first, with at most 10 pull requests, 5 releases, 5 new repositories and 5 videos so one busy kind cannot crowd out the rest.

| Field | Type | Meaning |
| --- | --- | --- |
| `type` | `"pr"`, `"release"`, `"repo"` or `"video"` | What it is. |
| `title` | string | PR title, release name, repository description or video title; untrusted text. |
| `url` | URL | Where it lives. |
| `date` | date | Merged, published, created or uploaded. |
| `repo` | string or null | `owner/name`; null for videos. |

### Summary

| Field | Type | Meaning |
| --- | --- | --- |
| `summary` | string or null | One plain-text paragraph about their week, when a writer produced one. |
| `summary_source` | string or null | Who wrote it: `"claude"`, `"gemini-3.5-flash-lite"`, `"human"` and so on. |
| `summary_is_ai` | boolean | True when an AI wrote it; show an "AI summary" label next to it. |
| `summary_written_at` | string or null | When it was written. |

A summary is only included while it is no older than the current week, so a stale summary never sits on top of new facts.

## Embedding: show who you follow on your own site

```html
<ul id="following"></ul>
<script>
  const isWeb = (u) => typeof u === "string" && /^https?:\/\//i.test(u);
  fetch("https://n8watkins.github.io/pr-post/feed.json")
    .then((r) => r.json())
    .then((feed) => {
      if (feed.schema_version !== 1) return;
      const list = document.getElementById("following");
      for (const person of feed.people) {
        const li = document.createElement("li");
        const name = document.createElement("a");
        name.textContent = person.who.name;
        if (isWeb(person.who.url)) name.href = person.who.url;
        li.append(name);
        const latest = person.recent[0];
        if (latest && isWeb(latest.url)) {
          const item = document.createElement("a");
          item.href = latest.url;
          item.textContent = latest.title;
          li.append(" shipped ", item);
        }
        if (person.summary) {
          const p = document.createElement("p");
          p.textContent = (person.summary_is_ai ? "AI summary: " : "") + person.summary;
          li.append(p);
        }
        list.append(li);
      }
    });
</script>
```

GitHub Pages serves `feed.json` with `Access-Control-Allow-Origin: *`, so the browser can fetch it from any site.
If you fetch it on a server instead, cache it: it only changes twice a week.

## Validating a feed

```sh
python3 scripts/feed.py --check dist/feed.json
```

This checks the file against the schema, rejects any key mentioning private work, and fails if the feed is empty.
