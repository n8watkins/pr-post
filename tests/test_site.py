"""The site: untrusted text becomes safe HTML, and a public site never prints private work."""

import os
import re
import unittest
from unittest import mock

from helpers import all_html, build_fixture, build_site
from fixture_paper import PRIVATE_COUNT, PRIVATE_REPO
from md import blocks, safe_url

import send_email


class SafeUrl(unittest.TestCase):
    def test_keeps_web_links(self):
        self.assertEqual(safe_url("https://github.com/x/y/pull/1"), "https://github.com/x/y/pull/1")
        self.assertEqual(safe_url("http://example.com"), "http://example.com")

    def test_drops_other_schemes(self):
        for url in ("javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "vbscript:x", "", None):
            self.assertEqual(safe_url(url), "#", url)


class Inline(unittest.TestCase):
    def test_site_escapes_and_blocks_script_links(self):
        out = build_site.inline('<img src=x onerror=alert(1)> [click](javascript:alert(1)) [ok](https://a.dev/?q=1&r=2)')
        self.assertNotIn("<img", out)
        self.assertNotIn("javascript:", out)
        self.assertIn('href="#"', out)
        self.assertIn('href="https://a.dev/?q=1&amp;r=2"', out)

    def test_email_blocks_script_links(self):
        out = send_email.inline("[x](javascript:alert(1)) **bold**")
        self.assertNotIn("javascript:", out)
        self.assertIn("<strong", out)


class Email(unittest.TestCase):
    def test_digest_carries_the_paper_name_and_blocks_script_links(self):
        md = "# Octo: Oct 1 to Oct 7\n\n## TL;DR\n- shipped [a parser](javascript:alert(1))\n- and <b>more</b>\n"
        page = send_email.render([(md, "x")], "weekly", [("Read on GitHub", "https://github.com/o/r")], "The Test Gazette")
        self.assertIn("The Test Gazette", page)
        self.assertNotIn("javascript:", page)
        self.assertNotIn("<b>more</b>", page)


class Markdown(unittest.TestCase):
    def test_parses_report_shapes(self):
        md = "# Title\n\n## TL;DR\n- one\n  continued\n- two\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
        kinds = [k for k, _ in blocks(md)]
        self.assertEqual(kinds, ["h1", "h2", "list", "table"])
        self.assertEqual(blocks(md)[2][1][1][0][1], "one continued")


class PublicBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.paper, cls.out, cls.feed = build_fixture()
        cls.html = all_html(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_pages_exist(self):
        for page in ("index.html", "archive/index.html", "404.html", "p/octo/index.html", "p/tuber/index.html", "feed.json", ".nojekyll"):
            self.assertTrue((self.out / page).exists(), page)

    def test_no_settings_page_on_a_public_site(self):
        self.assertFalse((self.out / "settings").exists())
        self.assertNotIn("People and settings", self.html)

    def test_never_prints_private_work(self):
        self.assertNotIn(str(PRIVATE_COUNT), self.html)
        self.assertNotIn(f"{PRIVATE_COUNT:,}", self.html)
        self.assertNotIn(PRIVATE_REPO, self.html)
        self.assertNotIn("Private launch plan", self.html)
        self.assertNotIn("Secret v9", self.html)
        self.assertNotRegex(self.html, r"\b1400\d\b")  # per-day private counts

    def test_untrusted_text_is_escaped(self):
        self.assertNotIn("javascript:", self.html)
        self.assertNotIn("<script>alert", self.html)
        self.assertNotIn("<img src=x", self.html)
        self.assertNotIn("<b>things</b>", self.html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", self.html)

    def test_facts_edition_is_labelled_and_uses_facts(self):
        front = (self.out / "index.html").read_text()
        self.assertIn("Facts edition", front)
        self.assertIn("no AI", front)
        self.assertIn("Octo Cat ships public-thing v1.2.0", front)
        self.assertIn("Tube Person", front)

    def test_youtube_only_person_renders(self):
        page = (self.out / "p" / "tuber" / "index.html").read_text()
        self.assertIn("On YouTube", page)
        self.assertIn("https://i.ytimg.com/vi/AAAAAAAAAA1/mqdefault.jpg", page)
        self.assertNotIn("Old video", page)  # outside the seven-day window
        self.assertNotIn("https://github.com/tuber", page)
        self.assertIn("videos this week", page)

    def test_public_site_is_indexable(self):
        self.assertNotIn('content="noindex', self.html)


class PrivateBuild(unittest.TestCase):
    def test_private_mode_can_show_private_counts_and_settings(self):
        tmp, _, out, feed = build_fixture('mode = "private"\nshow_private_counts = true')
        self.addCleanup(tmp.cleanup)
        html = all_html(out)
        self.assertIn(f"{PRIVATE_COUNT:,}", html)
        self.assertTrue((out / "settings" / "index.html").exists())
        self.assertIn('content="noindex', html)
        self.assertNotIn(str(PRIVATE_COUNT), str(feed))  # the feed stays public-only

    def test_show_private_counts_is_ignored_on_a_public_site(self):
        tmp, _, out, _ = build_fixture("show_private_counts = true")
        self.addCleanup(tmp.cleanup)
        self.assertNotIn(f"{PRIVATE_COUNT:,}", all_html(out))


class BasePath(unittest.TestCase):
    def test_links_carry_the_base_path(self):
        with mock.patch.dict(os.environ, {"PR_POST_BASE_PATH": "/pr-post", "PR_POST_SITE_URL": "https://me.github.io/pr-post"}):
            tmp, _, out, feed = build_fixture()
        self.addCleanup(tmp.cleanup)
        front = (out / "index.html").read_text()
        self.assertIn('href="/pr-post/assets/style.css"', front)
        self.assertIn('href="/pr-post/p/octo/"', front)
        root_links = re.findall(r'(?:href|src)="(/[^"]*)"', front)
        self.assertTrue(root_links and all(l.startswith("/pr-post/") for l in root_links), root_links)
        self.assertEqual(feed["people"][0]["who"]["url"], "https://me.github.io/pr-post/p/octo/")
        self.assertEqual(feed["feed_url"], "https://me.github.io/pr-post/feed.json")


class Summaries(unittest.TestCase):
    def test_ai_summary_is_labelled_on_the_page(self):
        tmp, _, out, feed = build_fixture(summaries={"2026-10-30.json": {"source": "claude", "people": {"octo": "Octo <i>shipped</i> a parser."}}})
        self.addCleanup(tmp.cleanup)
        page = (out / "p" / "octo" / "index.html").read_text()
        self.assertIn("AI summary · claude", page)
        self.assertIn("Octo &lt;i&gt;shipped&lt;/i&gt; a parser.", page)


if __name__ == "__main__":
    unittest.main()
