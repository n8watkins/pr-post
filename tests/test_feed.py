"""feed.json: valid against its schema, public facts only, safe URLs, AI summaries labelled."""

import copy
import json
import unittest
from pathlib import Path

from helpers import build_fixture
from fixture_paper import PRIVATE_COUNT, PRIVATE_REPO
import feed as feedlib

SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "docs" / "feed.schema.json").read_text())


class FeedFromFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.paper, cls.out, cls.feed = build_fixture()
        cls.text = (cls.out / "feed.json").read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_written_file_matches_returned_feed(self):
        self.assertEqual(json.loads(self.text), self.feed)

    def test_valid_against_schema(self):
        self.assertEqual(feedlib.validate(self.feed, SCHEMA), [])
        self.assertEqual(feedlib.check(self.feed), [])

    def test_versioned(self):
        self.assertEqual(self.feed["schema_version"], 1)
        self.assertEqual(SCHEMA["properties"]["schema_version"]["const"], feedlib.SCHEMA_VERSION)

    def test_no_private_counts_or_items(self):
        self.assertEqual(feedlib.private_keys(self.feed), [])
        self.assertNotIn(str(PRIVATE_COUNT), self.text)
        self.assertNotIn(PRIVATE_REPO, self.text)
        self.assertNotIn("Private launch plan", self.text)
        self.assertNotIn("Secret v9", self.text)

    def test_week_counts_are_public_only(self):
        octo = self.feed["people"][0]
        self.assertEqual(octo["week"]["public_prs_merged"], 2)  # the private PR is not counted
        self.assertNotIn("Evil link", [i["title"] for i in octo["recent"]])  # its unsafe link drops it from recent
        self.assertEqual(octo["week"]["days_active"], 4)        # days with public contributions, not private ones
        self.assertEqual(octo["week"]["releases"], 1)

    def test_urls_are_http_only(self):
        def urls(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    if k in ("url", "avatar", "home_page_url", "feed_url") and x is not None:
                        yield x
                    yield from urls(x)
            elif isinstance(v, list):
                for x in v:
                    yield from urls(x)
        found = list(urls(self.feed))
        self.assertTrue(found)
        for u in found:
            self.assertRegex(u, r"^https?://", u)
        self.assertNotIn("javascript:", self.text)

    def test_youtube_only_person(self):
        tuber = self.feed["people"][1]
        self.assertIsNone(tuber["who"]["github"])
        self.assertIsNone(tuber["week"]["public_prs_merged"])
        self.assertEqual(tuber["week"]["videos"], 1)
        self.assertEqual([i["type"] for i in tuber["recent"]], ["video"])
        self.assertEqual(tuber["recent"][0]["title"], "How I ship <img src=x onerror=alert(1)>")  # raw text: consumers must escape

    def test_recent_has_every_public_kind(self):
        kinds = {i["type"] for i in self.feed["people"][0]["recent"]}
        self.assertEqual(kinds, {"pr", "release", "repo"})

    def test_no_summary_without_a_writer(self):
        for p in self.feed["people"]:
            self.assertIsNone(p["summary"])
            self.assertIsNone(p["summary_source"])
            self.assertFalse(p["summary_is_ai"])


class SummaryHook(unittest.TestCase):
    def test_summary_is_carried_with_its_source(self):
        tmp, _, _, feed = build_fixture(summaries={"2026-10-31.json": {"source": "gemini-3.5-flash-lite", "written_at": "2026-10-31T16:00:00Z",
                                                                       "people": {"octo": "Shipped a parser.\nAnd a release."}}})
        self.addCleanup(tmp.cleanup)
        octo = feed["people"][0]
        self.assertEqual(octo["summary"], "Shipped a parser. And a release.")
        self.assertEqual(octo["summary_source"], "gemini-3.5-flash-lite")
        self.assertTrue(octo["summary_is_ai"])
        self.assertIsNone(feed["people"][1]["summary"])
        self.assertEqual(feedlib.check(feed), [])

    def test_human_summary_is_not_flagged_as_ai(self):
        tmp, _, _, feed = build_fixture(summaries={"2026-10-31.json": {"source": "human", "people": {"octo": "Written by me."}}})
        self.addCleanup(tmp.cleanup)
        self.assertFalse(feed["people"][0]["summary_is_ai"])

    def test_stale_summaries_are_ignored(self):
        tmp, _, _, feed = build_fixture(summaries={"2026-10-01.json": {"source": "claude", "people": {"octo": "Old news."}}})
        self.addCleanup(tmp.cleanup)
        self.assertIsNone(feed["people"][0]["summary"])


class Check(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp, _, _, cls.feed = build_fixture()
        tmp.cleanup()

    def test_empty_feed_fails(self):
        empty = {**self.feed, "people": []}
        self.assertIn("the feed has no people", feedlib.check(empty))

    def test_feed_with_no_recent_items_fails(self):
        quiet = copy.deepcopy(self.feed)
        for p in quiet["people"]:
            p["recent"] = []
        self.assertTrue(any("not one recent item" in e for e in feedlib.check(quiet)))

    def test_private_keys_fail(self):
        leaky = copy.deepcopy(self.feed)
        leaky["people"][0]["week"]["private_contributions"] = 5
        problems = feedlib.check(leaky)
        self.assertTrue(any("private counts must never" in e for e in problems))
        self.assertTrue(any("unexpected key private_contributions" in e for e in problems))

    def test_bad_urls_and_types_fail(self):
        bad = copy.deepcopy(self.feed)
        bad["people"][0]["recent"][0]["url"] = "javascript:alert(1)"
        bad["people"][0]["recent"][0]["type"] = "tweet"
        bad["people"][0]["week"]["videos"] = -1
        errors = feedlib.validate(bad, SCHEMA)
        self.assertTrue(any("not an http(s) URL" in e for e in errors), errors)
        self.assertTrue(any("tweet" in e for e in errors), errors)
        self.assertTrue(any("below 0" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
