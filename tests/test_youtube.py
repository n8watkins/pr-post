"""YouTube RSS parsing: the feed is untrusted, so ids are checked and links rebuilt."""

import datetime as dt
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts scripts/ on the path)
import youtube

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "youtube.xml").read_bytes()


class Parse(unittest.TestCase):
    def setUp(self):
        self.parsed = youtube.parse(FIXTURE)
        self.videos = self.parsed["videos"]

    def test_reads_channel_and_valid_entries_newest_first(self):
        self.assertEqual(self.parsed["channel"]["title"], "Example Builder | Shipping Things")
        self.assertEqual([v["id"] for v in self.videos], ["BBBBBBBBBB2", "AAAAAAAAAA1", "CCCCCCCCCC3"])

    def test_drops_entries_with_malformed_ids(self):
        self.assertNotIn("Malformed id is dropped", [v["title"] for v in self.videos])

    def test_collapses_whitespace_in_titles(self):
        self.assertEqual(self.videos[1]["title"], "I built an agent that writes my newsletter")

    def test_detects_shorts_and_reads_views(self):
        self.assertTrue(self.videos[0]["short"])
        self.assertEqual(self.videos[0]["url"], "https://www.youtube.com/shorts/BBBBBBBBBB2")
        self.assertFalse(self.videos[1]["short"])
        self.assertEqual(self.videos[1]["views"], 12345)
        self.assertIsNone(self.videos[0]["views"])

    def test_links_are_rebuilt_from_the_id_never_copied(self):
        evil = self.videos[2]
        self.assertEqual(evil["url"], "https://www.youtube.com/watch?v=CCCCCCCCCC3")
        self.assertEqual(evil["thumbnail"], "https://i.ytimg.com/vi/CCCCCCCCCC3/mqdefault.jpg")
        for v in self.videos:
            self.assertTrue(v["url"].startswith("https://www.youtube.com/"))
            self.assertTrue(v["thumbnail"].startswith("https://i.ytimg.com/vi/"))

    def test_keeps_markup_in_titles_as_plain_text_for_escaping_later(self):
        self.assertIn("<script>alert(1)</script>", self.videos[2]["title"])

    def test_caps_long_titles(self):
        long = FIXTURE.replace(b"One tip in 30 seconds", b"x" * 1000)
        title = youtube.parse(long)["videos"][0]["title"]
        self.assertLessEqual(len(title), youtube.TITLE_LIMIT)


class Rejects(unittest.TestCase):
    def test_doctype_and_entities(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><feed xmlns="http://www.w3.org/2005/Atom">&lol;</feed>'
        with self.assertRaises(youtube.FeedError):
            youtube.parse(bomb)

    def test_not_atom(self):
        with self.assertRaises(youtube.FeedError):
            youtube.parse(b"<rss><channel></channel></rss>")

    def test_not_xml(self):
        with self.assertRaises(youtube.FeedError):
            youtube.parse(b"<html>404")

    def test_too_large(self):
        with self.assertRaises(youtube.FeedError):
            youtube.parse(b" " * (youtube.MAX_BYTES + 1))


class Collect(unittest.TestCase):
    def test_keeps_videos_since_the_window_start(self):
        with mock.patch.object(youtube, "fetch", return_value=FIXTURE):
            out = youtube.collect("UCaaaaaaaaaaaaaaaaaaaaaa", dt.date(2026, 10, 25))
        self.assertEqual([v["id"] for v in out["videos"]], ["BBBBBBBBBB2", "AAAAAAAAAA1"])
        self.assertEqual(out["channel_url"], "https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(len(out["latest"]), 3)


if __name__ == "__main__":
    unittest.main()


class FeedFailure(unittest.TestCase):
    """A failed fetch keeps the previous run's videos instead of blanking the person."""

    person = {"id": "tuber", "github": None, "orgs": [], "youtube_channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa"}
    last = {"id": "tuber", "collected_at": "2026-10-28T11:00:00+00:00", "github": None, "errors": [],
            "youtube": {"channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa", "channel_url": "https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa",
                        "channel_title": "Tube", "videos": [{"id": "AAAAAAAAAA1", "title": "Old but recent"}], "latest": []}}

    def collect(self, previous):
        import collect
        with mock.patch.object(youtube, "collect", side_effect=youtube.FeedError("HTTP Error 404")), \
                mock.patch("sys.stderr"):
            return collect.collect_person(self.person, dt.date(2026, 10, 24), previous)

    def test_keeps_previous_videos_and_records_the_error(self):
        out = self.collect(self.last)
        self.assertEqual(out["youtube"]["videos"], self.last["youtube"]["videos"])
        self.assertEqual(out["youtube"]["carried_over_from"], "2026-10-28T11:00:00+00:00")
        self.assertEqual(out["errors"][0]["source"], "youtube")

    def test_carry_over_keeps_the_original_date_across_several_failures(self):
        once = {**self.collect(self.last), "collected_at": "2026-10-31T11:00:00+00:00"}
        self.assertEqual(self.collect(once)["youtube"]["carried_over_from"], "2026-10-28T11:00:00+00:00")

    def test_nothing_to_keep_on_a_first_run_or_a_changed_channel(self):
        self.assertIsNone(self.collect(None)["youtube"])
        other = {**self.last, "youtube": {**self.last["youtube"], "channel_id": "UCbbbbbbbbbbbbbbbbbbbbbb"}}
        self.assertIsNone(self.collect(other)["youtube"])
