"""Who to follow: guardrails, the mini report, scoring, picks, the page and the email. No network."""

import contextlib
import datetime as dt
import io
import re
import sys
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts scripts/ on the path)
import build_site
import config
import follow
import follow_lib as fl
import act_lib
import send_follow
from fixture_paper import write_paper

TODAY = dt.date(2026, 11, 7)

FOLLOW_TOML = """
[[people]]
github = "me-builder"
name = "you"
self = true

[follow]
enabled = true
stack_repos = ["acme/edge-sdk"]
picks = 2
cooldown_weeks = 8
rejump = 15

[follow.interests]
"Cloudflare" = ["cloudflare", "workers"]
"Local AI" = ["whisper", "llama.cpp"]
"Dev tools" = ["cli", "sdk"]
"""


def repo(name, stars=10, desc="", lang="TypeScript", topics=(), pushed="2026-11-01T00:00:00Z", release=None, owner="jane"):
    return {"nameWithOwner": f"{owner}/{name}", "name": name, "url": f"https://github.com/{owner}/{name}", "description": desc,
            "stargazerCount": stars, "pushedAt": pushed, "isArchived": False, "primaryLanguage": {"name": lang},
            "languages": {"nodes": [{"name": lang}, {"name": "CSS"}]}, "repositoryTopics": {"nodes": [{"topic": {"name": t}} for t in topics]},
            "latestRelease": release}


def user(**over):
    weeks = [{"contributionDays": [{"contributionCount": 3}] * 7} for _ in range(26)]
    u = {
        "login": "jane", "name": "Jane Doe \U0001F680", "bio": "Edge runtime at @acme. Mail me: jane@example.com or +1 (555) 123-4567",
        "company": "@acme", "location": "Lisbon, Portugal", "websiteUrl": "jane.dev", "twitterUsername": "janedoe",
        "avatarUrl": "https://avatars.githubusercontent.com/u/1?v=4", "createdAt": "2015-03-01T00:00:00Z",
        "followers": {"totalCount": 1200},
        "socialAccounts": {"nodes": [{"provider": "LINKEDIN", "url": "https://www.linkedin.com/in/jane-doe/"},
                                     {"provider": "INSTAGRAM", "url": "https://www.instagram.com/jane"},
                                     {"provider": "GENERIC", "url": "javascript:alert(1)"}]},
        "organizations": {"nodes": [{"login": "acme"}]},
        "pinnedItems": {"nodes": [repo("edge-sdk", 5000, "Cloudflare Workers SDK \u2014 fast", owner="acme",
                                       release={"name": "v2", "tagName": "v2", "publishedAt": "2026-10-20T00:00:00Z", "url": "https://github.com/acme/edge-sdk/releases/tag/v2"})]},
        "top": {"totalCount": 3, "nodes": [repo("whisper-cli", 900, "Local whisper.cpp dictation CLI", "Rust", ["cli"]),
                                           repo("dotfiles", 5, "", "Shell")]},
        "recent": {"nodes": [repo("notes", 1, "my notes", "Markdown")]},
        "contributed": {"nodes": [repo("edge-sdk", 5000, "Cloudflare Workers SDK", owner="acme"), repo("next.js", 130000, "The React Framework", "JavaScript", owner="vercel")]},
        "contributionsCollection": {"contributionCalendar": {"weeks": weeks}},
    }
    u.update(over)
    return u


SITE = {"url": "https://jane.dev/", "title": "Jane Doe", "description": "Jane builds edge tools. Contact jane@example.com",
        "talks": [{"title": "Talks", "url": "https://jane.dev/talks"}]}


def paper_with_follow():
    tmp = tempfile.TemporaryDirectory()
    paper = write_paper(Path(tmp.name) / "paper", 'mode = "private"\nurl = "https://paper.example"')
    wl = paper / "watchlist.toml"
    wl.write_text(wl.read_text() + FOLLOW_TOML + '\n[links]\nrepo = "https://github.com/me-builder/my-paper"\n')
    return tmp, paper


class Guardrails(unittest.TestCase):
    def test_clean_strips_contact_details_and_em_dashes(self):
        out = fl.clean("Write to a.b@example.com or call +44 20 7946 0958 \u2014 thanks")
        self.assertNotIn("@example.com", out)
        self.assertNotIn("7946", out)
        self.assertNotIn("\u2014", out)

    def test_location_is_city_and_country_at_most(self):
        self.assertEqual(fl.city_country("Lisbon, Portugal"), "Lisbon, Portugal")
        self.assertEqual(fl.city_country("Berlin"), "Berlin")
        self.assertEqual(fl.city_country("Austin, Texas, USA"), "Austin, USA")
        for bad in ("221B Baker Street, London", "94110", "a, b, c, d, e", ""):
            self.assertEqual(fl.city_country(bad), "", bad)

    def test_only_http_links_survive(self):
        self.assertIsNone(fl.url_or_none("javascript:alert(1)"))
        self.assertEqual(fl.url_or_none("jane.dev"), "https://jane.dev")


class MiniReport(unittest.TestCase):
    def setUp(self):
        self.r = fl.mini_report(user(), TODAY, SITE, [{"label": "Bluesky", "url": "https://bsky.app/profile/jane.dev", "verified_by": "rel=me"}],
                                [{"title": "Shipping the edge SDK", "url": "https://jane.dev/p/1", "date": "2026-10-01"}])
        self.text = json.dumps(self.r)

    def test_no_personal_contact_details_anywhere(self):
        self.assertNotIn("jane@example.com", self.text)
        self.assertNotIn("555", self.text)
        self.assertNotIn("\u2014", self.text)

    def test_every_fact_has_a_source(self):
        for w in self.r["work"]:
            self.assertTrue(config.is_http_url(w["source"]), w)
        for key in ("who", "location"):
            self.assertTrue(config.is_http_url(self.r[key]["source"]))
        for key in ("tech", "activity", "standing"):
            self.assertTrue(config.is_http_url(self.r[key]["source"]))
        for b in self.r["builds"]:
            self.assertTrue(config.is_http_url(b["url"]))

    def test_verified_company_and_professional_socials_only(self):
        self.assertIn("public member", self.r["work"][0]["text"])
        labels = {s["label"] for s in self.r["socials"]}
        self.assertEqual(labels, {"X", "LinkedIn", "Bluesky"})

    def test_tech_activity_and_standing(self):
        self.assertIn("Rust", self.r["tech"]["languages"])
        self.assertNotIn("CSS", self.r["tech"]["languages"])
        self.assertIn("Cloudflare Workers", self.r["tech"]["frameworks"])
        self.assertEqual(self.r["activity"]["last_12w"], 12 * 21)
        self.assertEqual(self.r["activity"]["trend"], "steady")
        self.assertEqual(self.r["standing"]["stars"], 906)  # own repos only, not the org's pinned one
        self.assertEqual(self.r["standing"]["maintains"], ["acme/edge-sdk"])
        self.assertEqual(self.r["releases"][0]["repo"], "acme/edge-sdk")

    def test_markdown_is_sourced(self):
        md = fl.render_md(self.r)
        self.assertIn("# Jane Doe (@jane)", md)
        self.assertIn("Lisbon, Portugal", md)
        self.assertIn("[Shipping the edge SDK](https://jane.dev/p/1)", md)
        self.assertNotIn("javascript:", md)


class Scoring(unittest.TestCase):
    def setUp(self):
        self.tmp, paper = paper_with_follow()
        self.cfg = config.load(paper)["follow"]
        vocab = fl.vocabulary()
        self.mine = fl.my_stack([fl._repo(repo("site", desc="Cloudflare Workers app", lang="TypeScript")),
                                 fl._repo(repo("dict", desc="whisper dictation", lang="Rust"))], vocab, TODAY)
        self.r = fl.mini_report(user(), TODAY, SITE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_score_is_bounded_and_explained(self):
        s = fl.score(self.r, {"followed_by": ["octo"], "stack": {"acme/edge-sdk": 4}}, self.mine, self.cfg, {"octo": "Octo Cat"})
        self.assertTrue(0 <= s["score"] <= 100)
        self.assertEqual(set(s["parts"]), {"stack", "activity", "quality", "relevance", "network"})
        self.assertIn("Octo Cat", s["why_follow"])
        self.assertIn("acme/edge-sdk", s["why_follow"])
        self.assertIn(s["recommendation"], ("Follow", "Maybe", "Skip"))

    def test_weights_are_tunable(self):
        cfg = {**self.cfg, "weights": {"stack": 0, "activity": 0, "quality": 0, "relevance": 0, "network": 1}}
        none = fl.score(self.r, {}, self.mine, cfg)
        some = fl.score(self.r, {"followed_by": ["a", "b", "c"]}, self.mine, cfg)
        self.assertEqual((none["score"], some["score"]), (0, 100))
        self.assertEqual(list(some["parts"]), ["network"])

    def test_pulse_refreshes_activity(self):
        quiet = fl.score(self.r, {}, self.mine, self.cfg, pulse={"followers": 1200, "weeks": [0] * 26})
        busy = fl.score(self.r, {}, self.mine, self.cfg)
        self.assertLess(quiet["parts"]["activity"]["points"], busy["parts"]["activity"]["points"])
        self.assertEqual(quiet["now"], {"last_12w": 0, "followers": 1200})  # what the picks deck shows
        self.assertEqual(busy["now"]["followers"], self.r["standing"]["followers"])


class Picks(unittest.TestCase):
    CFG = {"picks": 2, "cooldown_weeks": 8, "rejump": 15}

    def scored(self, **scores):
        return [{"login": k, "score": v, "recommendation": "Skip" if v < 45 else "Follow"} for k, v in scores.items()]

    def test_cooldown_and_rejump(self):
        state = {}
        self.assertEqual(fl.pick(self.scored(a=90, b=80, c=70, d=10), state, self.CFG, TODAY), ["a", "b"])
        later = TODAY + dt.timedelta(weeks=1)
        self.assertEqual(fl.pick(self.scored(a=90, b=96, c=70, d=10), state, self.CFG, later), ["b", "c"])
        much_later = TODAY + dt.timedelta(weeks=9)
        self.assertIn("a", fl.pick(self.scored(a=90, b=0, c=0), state, self.CFG, much_later))

    def test_skips_are_never_picked(self):
        self.assertEqual(fl.pick(self.scored(d=10), {}, self.CFG, TODAY), [])


class Discovery(unittest.TestCase):
    def test_choose_round_robins_sources(self):
        sig = {"a": {"followed_by": ["x", "y"]}, "b": {"followed_by": ["x"]}, "c": {"stack": {"r": 50}}, "d": {"similar": [{"repo": "q"}]}}
        self.assertEqual(follow.choose(list(sig), sig, 3), ["a", "c", "d"])

    def test_bots_and_orgs_are_excluded(self):
        for login, kind in (("renovate[bot]", "User"), ("release-bot", "User"), ("acme", "Organization"), ("x", "Bot")):
            self.assertTrue(follow.is_bot(login, kind), login)
        self.assertFalse(follow.is_bot("abbott", "User"))

    def test_client_stops_at_its_budget_and_before_the_rate_limit(self):
        class Resp(io.BytesIO):
            headers = {"X-RateLimit-Remaining": "100", "X-RateLimit-Resource": "core"}
            def __enter__(self): return self
            def __exit__(self, *a): return False
        gh = follow.GitHub("t", budget=5, reserve=150, opener=lambda req, timeout: Resp(b"[]"))
        gh.rest("/x")
        with self.assertRaises(follow.OutOfBudget):
            gh.rest("/y")  # 100 left is under the reserve of 150

        def not_found(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        self.assertIsNone(follow.GitHub("t", 5, opener=not_found).rest("/z"))


class Output(unittest.TestCase):
    def setUp(self):
        self.tmp, self.paper = paper_with_follow()
        rep = fl.mini_report(user(), TODAY, SITE)
        (self.paper / "follow" / "people").mkdir(parents=True)
        (self.paper / "follow" / "people" / "jane.json").write_text(json.dumps(rep))
        s = fl.score(rep, {}, {"TypeScript": 2}, config.load(self.paper)["follow"])
        latest = {"date": dt.date.today().isoformat(), "picks": ["jane"], "stack": ["TypeScript"],
                  "candidates": [{"login": "jane", "name": "Jane Doe", "avatar": rep["avatar"], "profile": rep["profile"],
                                  "researched": rep["researched"], **s, "found": [{"text": "Followed by Octo", "url": "https://github.com/octo?tab=following"}]}]}
        (self.paper / "follow" / "latest.json").write_text(json.dumps(latest))

    def tearDown(self):
        self.tmp.cleanup()

    def test_private_site_has_the_page_and_actions(self):
        out = Path(self.tmp.name) / "dist"
        build_site.build(self.paper, out)
        page = (out / "follow" / "index.html").read_text()
        self.assertIn("Who to follow", (out / "index.html").read_text())
        self.assertIn('href="https://github.com/jane"', page)
        self.assertIn("gh workflow run follow-add.yml --repo me-builder/my-paper -f github=jane", page)
        self.assertNotIn("jane@example.com", page)

    def test_a_paper_kept_in_git_copies_the_add_command(self):
        out = Path(self.tmp.name) / "dist"
        build_site.build(self.paper, out)
        page = (out / "follow" / "index.html").read_text()
        cmd = "gh workflow run follow-add.yml --repo me-builder/my-paper -f github=jane"
        self.assertIn(f'data-copy="{cmd}"', page)
        self.assertNotIn("data-draft=", page)
        self.assertIn("Open GitHub profile", page)
        self.assertIn('href="/follow/picks/"', page)
        self.assertIn('<script src="/assets/follow.js" defer></script>', page)
        picks = (out / "follow" / "picks" / "index.html").read_text()
        data = json.loads(re.search(r'<script type="application/json" id="picks-data">(.*?)</script>', picks, re.S).group(1))
        self.assertEqual([c["login"] for c in data["cards"]], ["jane"])
        self.assertEqual(data["cards"][0]["cmd"], cmd)
        self.assertNotIn("data-api=", picks)
        self.assertIn("copy the command that adds them", picks)

    def test_a_paper_kept_in_cloudflare_drafts_through_the_people_page(self):
        wl = self.paper / "watchlist.toml"
        wl.write_text(wl.read_text().replace('mode = "private"', 'mode = "private"\nstorage = "cloudflare"', 1))
        out = Path(self.tmp.name) / "dist"
        build_site.build(self.paper, out)
        page = (out / "follow" / "index.html").read_text()
        self.assertIn('data-draft="jane" data-api="/api/people/drafts"', page)
        self.assertNotIn("data-copy=", page)
        self.assertNotIn("gh workflow run follow-add.yml", page)  # the command is not needed here
        picks = (out / "follow" / "picks" / "index.html").read_text()
        self.assertIn('data-api="/api/people/drafts" data-people="/people/"', picks)
        data = json.loads(re.search(r'<script type="application/json" id="picks-data">(.*?)</script>', picks, re.S).group(1))
        self.assertNotIn("cmd", data["cards"][0])

    def test_the_deck_shows_the_numbers_the_score_used_and_no_name_can_break_the_page(self):
        latest = json.loads((self.paper / "follow" / "latest.json").read_text())
        latest["candidates"][0]["now"] = {"last_12w": 7, "followers": 4321}
        latest["candidates"][0]["name"] = "x<!--<script></script>"
        (self.paper / "follow" / "latest.json").write_text(json.dumps(latest))
        out = Path(self.tmp.name) / "dist"
        build_site.build(self.paper, out)
        picks = (out / "follow" / "picks" / "index.html").read_text()
        raw = re.search(r'<script type="application/json" id="picks-data">(.*?)</script>', picks, re.S).group(1)
        self.assertNotIn("<", raw)
        card = json.loads(raw)["cards"][0]
        self.assertEqual(card["name"], "x<!--<script></script>")
        self.assertEqual((card["active"], card["followers"]), (7, 4321))

    def test_the_deck_deals_picks_then_maybes_and_never_skips(self):
        latest = json.loads((self.paper / "follow" / "latest.json").read_text())
        jane = latest["candidates"][0]
        maybe = {**jane, "login": "may", "recommendation": "Maybe"}
        skip = {**jane, "login": "sam", "recommendation": "Skip"}
        latest["candidates"] = [skip, maybe, jane]
        picks, rest = build_site.deck_order(latest)
        self.assertEqual([c["login"] for c in picks], ["jane"])
        self.assertEqual([c["login"] for c in rest], ["may"])

    def test_public_site_leaves_it_out(self):
        wl = self.paper / "watchlist.toml"
        wl.write_text(wl.read_text().replace('mode = "private"', 'mode = "public"'))
        out = Path(self.tmp.name) / "dist"
        build_site.build(self.paper, out)
        self.assertFalse((out / "follow").exists())
        self.assertNotIn("Who to follow", (out / "index.html").read_text())

    def test_follow_email(self):
        watch = config.load(self.paper)
        latest, reports = fl.load_latest(self.paper, watch, max_age_days=8)
        page, text, subject = send_follow.render(latest, reports, "https://paper.example", "https://github.com/me-builder/my-paper", "", "The Gazette")
        self.assertIn("Who to follow", subject)
        self.assertIn("https://paper.example/follow/#jane", page)
        self.assertIn('href="https://github.com/jane"', page)
        self.assertIn("Full report", page)
        self.assertNotIn("/act/", page)  # no signing key: plain links
        stale, _ = fl.load_latest(self.paper, watch, max_age_days=8, today=dt.date.today() + dt.timedelta(days=30))
        self.assertIsNone(stale)

    def test_follow_email_one_click_links_are_signed(self):
        watch = config.load(self.paper)
        latest, reports = fl.load_latest(self.paper, watch, max_age_days=8)
        page, _, _ = send_follow.render(latest, reports, "https://paper.example", None, "k" * 32, "The Gazette")
        tokens = re.findall(r"https://paper\.example/act/([A-Za-z0-9_.-]+)", page)
        self.assertEqual(sorted(act_lib.verify("k" * 32, t)["a"] for t in tokens), ["add", "follow"])
        self.assertNotIn("k" * 32, page)

    def test_follow_email_stays_off_until_turned_on(self):
        out = io.StringIO()
        argv = ["send_follow.py", "--paper", str(self.paper), "--dry-run"]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            send_follow.main()
        self.assertIn("[follow] email = false", out.getvalue())


class Config(unittest.TestCase):
    def test_bad_follow_settings_are_refused(self):
        base = '[[people]]\ngithub = "me"\nself = true\n[follow]\nenabled = true\n'
        for extra in ('stack_repos = ["not a repo"]', "[follow.weights]\nstack = -1", "[follow.weights]\nfame = 3",
                      "[follow.thresholds]\nfollow = 40\nmaybe = 60", '[follow.interests]\n"X" = []'):
            with self.assertRaises(config.ConfigError, msg=extra):
                config.parse(base + extra)
        self.assertEqual(config.parse(base)["follow"]["me"], "me")


if __name__ == "__main__":
    unittest.main()


class Draft(unittest.TestCase):
    def test_draft_adds_entry_before_self_and_writes_profile(self):
        tmp, paper = paper_with_follow()
        self.addCleanup(tmp.cleanup)
        fake = {"facts": {"login": "jane", "name": "Jane Doe", "bio": "Edge tools", "followers": 1, "created": "2015-01-01", "company": "",
                          "location": "", "orgs": [], "website": None, "links": [], "pinned": [], "top": [], "recent": [], "outside": []},
                "site": {"socials": [], "feeds": []}, "links": [], "youtube": {"id": None, "title": None, "source": None},
                "orgs": [], "ticker": "JANE", "researched": "2026-11-07"}
        orig = follow.rp.research
        follow.rp.research = lambda login, paper=None: fake
        self.addCleanup(setattr, follow.rp, "research", orig)
        follow.draft(paper, config.load(paper), "jane", "Builds edge tools")
        cfg = config.load(paper)
        ids = [p["id"] for p in cfg["people"]]
        self.assertEqual(ids[-2:], ["jane", "me-builder"])
        self.assertEqual(cfg["people"][-2]["known_for"], "Builds edge tools")
        self.assertIn("Why I follow them: Builds edge tools", (paper / "profiles" / "jane.md").read_text())
