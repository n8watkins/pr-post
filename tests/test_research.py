"""research_person.py: link classification, ownership checks and the draft it writes. No network."""

import re
import tomllib
import unittest

import helpers  # noqa: F401  (puts scripts/ on the path)
import config
import research_person as rp

SITE = """<html><head>
<link rel="alternate" type="application/rss+xml" href="/rss.xml">
<link rel="me" href="https://hachyderm.io/@janedoe">
</head><body>
<a href="https://x.com/janedoe">X</a>
<a href="https://twitter.com/janedoe">X again, old domain</a>
<a href="https://x.com/someoneelse/status/123">a tweet</a>
<a href="https://twitter.com/intent/tweet?text=hi">share</a>
<a href="https://www.linkedin.com/in/jane-doe-123/">LinkedIn</a>
<a href="https://www.linkedin.com/company/acme">employer</a>
<a href="https://www.youtube.com/@acmecorp">employer channel</a>
<a href="https://janedoe.substack.com/">newsletter</a>
<a href="https://janedoe.substack.com/p/one-post">one post</a>
<a href="javascript:alert(1)">evil</a>
<a href="https://github.com/janedoe">GitHub</a>
</body></html>"""


class Classify(unittest.TestCase):
    def test_profiles_are_recognized(self):
        cases = {
            "https://x.com/janedoe": "X",
            "https://www.linkedin.com/in/janedoe/": "LinkedIn",
            "https://bsky.app/profile/jane.example.com": "Bluesky",
            "https://www.youtube.com/@jane": "YouTube",
            "https://www.youtube.com/channel/UCnCikd0s4i9KoDtaHPlK-JA": "YouTube",
            "https://jane.substack.com": "Substack",
            "https://mastodon.social/@jane": "Mastodon",
            "https://example.social/@jane": "Mastodon",
            "https://discord.gg/abc123": "Discord",
        }
        for url, label in cases.items():
            self.assertEqual(rp.classify(url), label, url)

    def test_posts_shares_companies_and_home_pages_are_not_profiles(self):
        for url in ("https://x.com/jane/status/1", "https://twitter.com/intent/tweet", "https://x.com/",
                    "https://www.linkedin.com/company/acme", "https://www.youtube.com/watch?v=abc",
                    "https://jane.substack.com/p/post", "https://substack.com/notes/post/p-1",
                    "https://www.reddit.com/submit?url=x", "https://example.com/about", "javascript:alert(1)"):
            self.assertIsNone(rp.classify(url), url)


class SiteLinks(unittest.TestCase):
    def setUp(self):
        self.found = rp.site_links(SITE, "https://janedoe.dev/")

    def test_keeps_profiles_once_and_normalizes_twitter(self):
        urls = [s["url"] for s in self.found["socials"]]
        self.assertEqual(urls.count("https://x.com/janedoe"), 1)
        self.assertNotIn("https://twitter.com/janedoe", urls)
        self.assertIn("https://www.linkedin.com/in/jane-doe-123/", urls)
        self.assertIn("https://janedoe.substack.com/", urls)

    def test_drops_posts_and_unsafe_links(self):
        urls = " ".join(s["url"] for s in self.found["socials"])
        self.assertNotIn("status", urls)
        self.assertNotIn("javascript", urls)
        self.assertNotIn("/p/one-post", urls)

    def test_reads_rel_me_and_feeds(self):
        me = [s for s in self.found["socials"] if s["source"] == "rel=me"]
        self.assertEqual([s["url"] for s in me], ["https://hachyderm.io/@janedoe"])
        self.assertEqual(self.found["feeds"], ["https://janedoe.dev/rss.xml"])

    def test_survives_garbage(self):
        self.assertEqual(rp.site_links("<a href=", "https://x.example/"), {"socials": [], "feeds": []})


class Ownership(unittest.TestCase):
    tokens = rp.name_tokens("janedoe", "Jane Doe", "jdoe_codes")

    def test_matching_handles_are_theirs(self):
        for url in ("https://x.com/janedoe", "https://www.linkedin.com/in/jane-doe-123/",
                    "https://janedoe.substack.com", "https://bsky.app/profile/janedoe.bsky.social",
                    "https://x.com/jdoe_codes"):
            self.assertTrue(rp.looks_like_theirs(url, self.tokens), url)

    def test_employer_accounts_are_not(self):
        for url in ("https://www.youtube.com/@acmecorp", "https://x.com/pika_labs", "https://discord.gg/pika"):
            self.assertFalse(rp.looks_like_theirs(url, self.tokens), url)


class Helpers(unittest.TestCase):
    def test_clean_name_drops_emoji(self):
        self.assertEqual(rp.clean_name("Daniel Miessler \U0001f6e1️"), "Daniel Miessler")
        self.assertEqual(rp.clean_name("Tobi Lütke"), "Tobi Lütke")

    def test_ticker_avoids_taken_symbols(self):
        self.assertEqual(rp.suggest_ticker("Jane Doe", "janedoe", set()), "DOE")
        self.assertEqual(rp.suggest_ticker("Jane Doe", "janedoe", {"DOE"}), "JDOE")
        self.assertTrue(config.TICKER_RE.match(rp.suggest_ticker("\U0001f600", "x-y", set())))


def findings(**over):
    facts = {"login": "janedoe", "name": "Jane Doe", "bio": "Builds things", "company": "", "location": "Oslo",
             "followers": 10, "created": "2015-01-01", "website": "https://janedoe.dev/",
             "links": [], "orgs": ["acme"], "pinned": None, "outside": [("acme/tool", 5)],
             "top": [{"repo": "janedoe/tool", "description": "A tool", "stars": 5, "url": "https://github.com/janedoe/tool", "pushed": "2026-10-01", "fork": False}],
             "recent": [{"repo": "janedoe/tool", "description": "A tool", "stars": 5, "url": "https://github.com/janedoe/tool", "pushed": "2026-10-01", "fork": False}]}
    r = {"facts": facts, "site": {"socials": [], "feeds": ["https://janedoe.dev/rss.xml"], "status": 200, "feed_checks": {"https://janedoe.dev/rss.xml": "ok"}},
         "links": [
             {"label": "X", "url": "https://x.com/janedoe", "source": "the X field on their GitHub profile", "theirs": True, "check": "manual"},
             {"label": "Substack", "url": "https://janedoe.substack.com/", "source": "linked from their website", "theirs": True, "check": "ok"},
             {"label": "YouTube", "url": "https://www.youtube.com/@acmecorp", "source": "linked from their website", "theirs": False, "check": "ok"},
             {"label": "Bluesky", "url": "https://bsky.app/profile/janedoe.dev", "source": "linked from their website", "theirs": True, "check": "broken (404)"},
         ],
         "youtube": {"id": "UCnCikd0s4i9KoDtaHPlK-JA", "title": "Jane Builds", "source": "--youtube"},
         "orgs": ["acme"], "ticker": "DOE", "researched": "2026-11-02"}
    r.update(over)
    return r


class Render(unittest.TestCase):
    def setUp(self):
        self.text = rp.render(findings())

    def toml_block(self):
        block = re.search(r"<!--[^\n]*\n\n(.*?)-->", self.text, re.DOTALL)[1]
        return tomllib.loads(block)["people"][0]

    def test_watchlist_draft_is_valid_config(self):
        person = config._person(self.toml_block(), 0)
        self.assertEqual(person["id"], "janedoe")
        self.assertEqual(person["orgs"], ["acme"])
        self.assertEqual(person["youtube_channel_id"], "UCnCikd0s4i9KoDtaHPlK-JA")

    def test_draft_links_only_theirs_working_and_not_already_on_github(self):
        urls = [l["url"] for l in self.toml_block()["links"]]
        self.assertIn("https://janedoe.substack.com/", urls)
        self.assertIn("https://www.youtube.com/channel/UCnCikd0s4i9KoDtaHPlK-JA", urls)
        self.assertNotIn("https://x.com/janedoe", urls)  # collected from GitHub automatically
        self.assertNotIn("https://www.youtube.com/@acmecorp", urls)  # employer
        self.assertNotIn("https://bsky.app/profile/janedoe.dev", urls)  # broken

    def test_profile_separates_unowned_links_and_lists_what_to_search(self):
        mine, others = self.text.split("Also linked from their website")[0], self.text.split("Also linked from their website")[1]
        self.assertNotIn("acmecorp", mine)
        self.assertIn("acmecorp", others.split("## Notable projects")[0])
        self.assertIn("LinkedIn: search for", self.text)
        self.assertNotIn("X: search for", self.text)
        self.assertIn("open it by hand", self.text)

    def test_no_em_dashes(self):
        self.assertNotIn(chr(0x2014), self.text)


if __name__ == "__main__":
    unittest.main()
