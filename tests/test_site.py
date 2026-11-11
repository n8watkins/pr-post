"""The site: untrusted text becomes safe HTML, and a public site never prints private work."""

import contextlib
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import all_html, build_fixture, build_site, config
from fixture_paper import PRIVATE_COUNT, PRIVATE_REPO, write_paper
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

    def test_email_links_block_script_urls(self):
        out = send_email.E.link("javascript:alert(1)", "x")
        self.assertNotIn("javascript:", out)
        self.assertIn('href="#"', out)


class Email(unittest.TestCase):
    WEEKLY = ("# Octo: Oct 1 to Oct 7\n\n## TL;DR\n- Shipped [a parser](javascript:alert(1)) that reads TOML. And more.\n- and <b>more</b>\n\n"
              "## Where the time went\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n## parser\n**What it is:** long analysis that stays on the web.\n")
    QUIET = "# Tuber update: Oct 5 to Oct 7\n\nNothing notable since the weekly report.\n"

    def render(self, edition=None, url="https://paper.example/r/2026-10-07-weekly/"):
        items = [({"id": "octo", "name": "Octo"}, self.WEEKLY, url and url + "#octo"), ({"id": "tuber", "name": "Tuber"}, self.QUIET, url and url + "#tuber")]
        return send_email.render(items, "weekly", "2026-10-07-weekly", edition, url, [("Read on GitHub", "https://github.com/o/r")], "The Test Gazette")

    def test_digest_is_short_and_safe(self):
        page, text, subject = self.render()
        self.assertIn("The Test Gazette", page)
        self.assertNotIn("javascript:", page + text)
        self.assertNotIn("<b>more</b>", page)
        # One line per person and the button; the long analysis stays on the web.
        self.assertIn("Shipped a parser that reads TOML.", page)
        self.assertNotIn("long analysis", page + text)
        self.assertNotIn("<details", page)
        self.assertIn("Read the full edition", page)
        self.assertIn('href="https://paper.example/r/2026-10-07-weekly/#octo"', page)
        self.assertIn("Quiet this time", page)
        self.assertEqual(subject, "Weekly report: 2 people, Oct 1 to Oct 7")
        self.assertIn("Read the full edition: https://paper.example/r/2026-10-07-weekly/", text)

    def test_tldr_prefers_the_edition(self):
        page, _, _ = self.render(edition={"tldr": ["Octo ships a **parser**.", "Tuber rests."], "lead": {"headline": "Not this"}})
        self.assertIn("Octo ships a parser.", page)
        self.assertNotIn("Not this", page)
        page, _, _ = self.render(edition={"lead": {"headline": "Parser lands"}, "stories": [{"headline": "Tuber rests"}]})
        self.assertIn("Parser lands", page)

    def test_without_a_site_there_is_no_button(self):
        page, _, _ = self.render(url=None)
        self.assertNotIn("Read the full edition", page)

    def test_works_in_outlook_and_dark_mode(self):
        page, _, _ = self.render()
        self.assertIn("<!--[if mso]>", page)
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertIn('name="color-scheme" content="light dark"', page)
        self.assertLess(len(page.encode()), 102_000)  # Gmail clips longer emails


class EmailRun(unittest.TestCase):
    """send_email.py end to end against a paper in its own repository, with the Worker never reachable."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "private-paper"
        (self.repo / ".git").mkdir(parents=True)
        write_paper(self.repo, 'mode = "private"')
        with open(self.repo / "watchlist.toml", "a") as f:
            f.write('\n[email]\nto = "reader@example.com"\n\n[delivery]\nemail = true\n\n[links]\nrepo = "https://github.com/me/private-paper"\n')
        self.report = self.repo / "reports" / "octo" / "2026-10-31-weekly.md"
        self.report.parent.mkdir(parents=True)
        self.report.write_text("# Octo Cat: Oct 25 to Oct 31\n\n## TL;DR\n- shipped a parser\n")

    def run_main(self, *args, env=None):
        out = io.StringIO()
        argv = ["send_email.py", "--paper", str(self.repo), str(self.report), *args]
        never = mock.Mock(side_effect=AssertionError("tried to contact the Worker"))
        with mock.patch.object(sys, "argv", argv), mock.patch.dict(os.environ, env or {}, clear=False), \
                mock.patch("urllib.request.urlopen", never), contextlib.redirect_stdout(out):
            send_email.main()
        return out.getvalue()

    def test_dry_run_checks_everything_and_sends_nothing(self):
        preview = self.repo / "digest.html"
        out = self.run_main("--dry-run", "--preview", str(preview), env={"PR_POST_API": "https://paper.example", "PR_POST_TOKEN": "not-a-real-token-but-long-enough-xx"})
        self.assertIn("Dry run", out)
        self.assertIn("through the paper's Worker (PR_POST_API and PR_POST_TOKEN set)", out)
        self.assertNotIn("Octo Cat", out)  # subjects name people; dry runs can land in public Actions logs
        self.assertNotIn("not-a-real-token", out)
        self.assertNotIn("reader@example.com", out)
        page = preview.read_text()
        self.assertIn("The Test Gazette", page)
        # Without [site] url, the link points into the paper's own repository, not the engine's.
        self.assertIn("https://github.com/me/private-paper/blob/main/reports/octo/2026-10-31-weekly.md", page)

    def test_one_email_per_issue(self):
        other = self.repo / "reports" / "octo" / "2026-10-28-update.md"
        other.write_text("# Octo Cat update: Oct 27 to Oct 28\n\n- one\n")
        argv = ["send_email.py", "--paper", str(self.repo), str(self.report), str(other), "--dry-run"]
        with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
            send_email.main()

    def test_old_email_keys_are_ignored_and_a_suppressed_recipient_stops_it(self):
        # A watch list from before the Worker sent email still loads; its provider keys do nothing now.
        text = (self.repo / "watchlist.toml").read_text().replace('to = "reader@example.com"', 'to = "reader@example.com"\nvia = "ses"\nconfiguration_set = "pr-post"\nfrom = "PR Post <onboarding@resend.dev>"')
        (self.repo / "watchlist.toml").write_text(text)
        out = self.run_main("--dry-run", env={"PR_POST_API": "", "PR_POST_TOKEN": ""})
        self.assertIn("through the paper's Worker (PR_POST_API and PR_POST_TOKEN not set)", out)
        (self.repo / "suppressed.json").write_text('[{"address": "Reader@Example.com", "reason": "Permanent bounce", "at": "2026-11-09T10:00:00Z"}]')
        with self.assertRaises(SystemExit) as stop:
            self.run_main("--dry-run")
        self.assertIn("Permanent bounce on 2026-11-09", str(stop.exception))

    def test_dry_run_still_respects_email_being_off(self):
        text = (self.repo / "watchlist.toml").read_text().replace("email = true", "email = false")
        (self.repo / "watchlist.toml").write_text(text)
        self.assertIn("turned off", self.run_main("--dry-run"))


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

    def test_a_paper_kept_in_cloudflare_gets_the_people_page(self):
        tmp, _, out, _ = build_fixture('mode = "private"\nstorage = "cloudflare"')
        self.addCleanup(tmp.cleanup)
        page = (out / "people" / "index.html").read_text()
        self.assertIn('id="people-app" data-api="/api/people"', page)
        self.assertIn('<a href="/people/" aria-current="page">People</a>', page)
        self.assertFalse((out / "settings").exists())
        self.assertNotIn("watchlist-data", all_html(out))  # the watch list is fetched after sign-in, never baked into a page

    def test_storage_must_be_known(self):
        with self.assertRaises(config.ConfigError):
            config.parse('[site]\nstorage = "s3"\n')

    def test_show_private_counts_is_ignored_on_a_public_site(self):
        tmp, _, out, _ = build_fixture("show_private_counts = true")
        self.addCleanup(tmp.cleanup)
        self.assertNotIn(f"{PRIVATE_COUNT:,}", all_html(out))


def facts_stats(**over):
    """The smallest stats entry facts_story and facts_edition need."""
    st = {"public_merged_prs": 0, "releases": [], "videos": [], "births": [], "deaths": [], "repos": [], "commits": 0,
          "recent": [], "has_github": True, "has_youtube": False, "window": {"start": "2026-10-25", "end": "2026-10-31"}}
    return {**st, **over}


class FactsEdition(unittest.TestCase):
    def test_the_reader_is_written_in_the_second_person(self):
        st = facts_stats(public_merged_prs=3, repos=[{"repo": "me/app", "merged_prs": 3, "commits": 0}])
        me = {"id": "me", "name": "Nat", "self": True}
        self.assertEqual(build_site.facts_story(me, st)["headline"], "You merge 3 PRs in app")
        self.assertEqual(build_site.facts_story({**me, "self": False}, st)["headline"], "Nat merges 3 PRs in app")
        self.assertEqual(build_site.facts_story(me, facts_stats())["headline"], "A quiet week for you")

    def test_side_column_keeps_the_newest_few_births(self):
        births = [{"repo": f"o/r{i}", "url": f"https://github.com/o/r{i}", "description": "", "date": f"2026-10-{20 + i:02d}"} for i in range(9)]
        people = [{"id": "o", "name": "O", "ticker": "O"}]
        e = build_site.facts_edition(people, {"o": facts_stats(births=births)})
        self.assertEqual([b["repo"] for b in e["births_html"]], ["o/r8", "o/r7", "o/r6", "o/r5", "o/r4", "o/r3"])
        self.assertEqual(e["births_more"], 3)
        build_site.configure(config.parse("")["site"])
        page = build_site.front_page(e, people, [{"date": "2026-10-31", "people": {"o": facts_stats(births=births)}}], 0)
        side = page[page.index('<div class="side">'):]
        self.assertIn("Plus 3 more new repos", side)
        self.assertNotIn("o/r2", side)


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


class IssuePage(unittest.TestCase):
    """The web edition of one writer run: the page the email's button opens."""

    def test_issue_page_has_contents_and_folded_sections(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        paper = write_paper(Path(tmp.name) / "paper", 'mode = "private"')
        (paper / "reports" / "octo").mkdir(parents=True)
        (paper / "reports" / "octo" / "2026-10-31-weekly.md").write_text(
            "# Octo Cat: Oct 25 to Oct 31\n\n## TL;DR\n- Shipped [a parser](https://github.com/octo/p/pull/1).\n\n## parser\n**What it is:** a parser.\n")
        (paper / "editions").mkdir()
        (paper / "editions" / "2026-10-31-weekly.json").write_text(
            '{"date": "2026-10-31", "kind": "weekly", "tldr": ["Octo ships a parser."], "lead": {"id": "octo", "headline": "Parser!", "deck": "", "body": [], "sources": []}, "stories": []}')
        cfg = config.load(paper)
        import stats  # noqa: PLC0415
        snap = stats.build(paper, cfg)
        (paper / "stats").mkdir()
        (paper / "stats" / f"{snap['date']}.json").write_text(__import__("json").dumps(snap))
        out = Path(tmp.name) / "dist"
        with contextlib.redirect_stdout(io.StringIO()):
            build_site.build(paper, out)
        page = (out / "r" / "2026-10-31-weekly" / "index.html").read_text()
        self.assertIn('<a href="#octo">Octo Cat</a>', page)
        self.assertIn('<section class="issue-person" id="octo">', page)
        self.assertIn('<details class="rsect" open><summary>TL;DR</summary>', page)
        self.assertIn('<details class="rsect"><summary>parser</summary>', page)
        self.assertIn("Octo ships a parser.", page)
        self.assertIn("/r/2026-10-31-weekly/", (out / "archive" / "index.html").read_text())


class Briefs(unittest.TestCase):
    def test_brief_is_short_and_never_names_private_repos(self):
        import brief  # noqa: PLC0415
        st = {"window": {"start": "2026-10-25", "end": "2026-10-31"}, "public_merged_prs": 1, "opened_prs": 2, "commits": 3, "repos_touched": 2,
              "releases": [], "videos": [], "births": [], "deaths": [], "contributions": {"public": 5, "private": 7},
              "repos": [{"repo": "octo/p", "merged_prs": 1, "commits": 3, "private": False}, {"repo": "octo/secret", "merged_prs": 0, "commits": 9, "private": True}]}
        data = {"collected_at": "2026-10-31T11:00:00Z", "since": "2026-10-23", "github": {
            "pull_requests": [{"repo": "octo/p", "number": 1, "title": "Parser", "state": "closed", "created": "2026-10-30", "merged": "2026-10-30",
                               "url": "https://github.com/octo/p/pull/1", "body": "x " * 2000},
                              {"repo": "octo/secret", "number": 2, "title": "Secret thing", "state": "closed", "created": "2026-10-30", "merged": "2026-10-30",
                               "url": "https://github.com/octo/secret/pull/2", "body": ""}],
            "commits": [], "readmes": {"octo/p": "# P\n[![badge](x)](y)\nP parses things."}}}
        text = brief.brief({"id": "octo", "name": "Octo"}, data, st, "", [])
        self.assertIn("octo/p#1 Parser", text)
        self.assertIn("1 private repos", text)
        self.assertNotIn("secret", text.replace("never describe", ""))
        self.assertIn("P parses things.", text)
        self.assertLess(len(text), 2000)
