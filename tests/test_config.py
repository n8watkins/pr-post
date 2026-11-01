"""watchlist.toml parsing: sensible defaults and loud errors."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
import config

DEMO = Path(__file__).resolve().parent.parent / "demo"


class Defaults(unittest.TestCase):
    def test_empty_file_gets_defaults(self):
        cfg = config.parse("")
        self.assertEqual(cfg["site"]["title"], "The Pull Request Post")
        self.assertEqual(cfg["site"]["mode"], "public")
        self.assertEqual(cfg["writer"]["kind"], "none")
        self.assertFalse(cfg["delivery"]["email"])
        self.assertEqual(cfg["people"], [])

    def test_private_counts_need_private_mode(self):
        self.assertFalse(config.private_counts(config.parse("[site]\nshow_private_counts = true")["site"]))
        self.assertTrue(config.private_counts(config.parse('[site]\nmode = "private"\nshow_private_counts = true')["site"]))

    def test_demo_watchlist_is_valid(self):
        cfg = config.load(DEMO)
        ids = [p["id"] for p in cfg["people"]]
        self.assertEqual(ids, ["kunchenguid", "moomooskycow", "nateherk"])
        nate = cfg["people"][2]
        self.assertIsNone(nate["github"])
        self.assertEqual(nate["youtube_channel_id"], "UC2ojq-nuP8ceeHqiroeKhBA")


class People(unittest.TestCase):
    def test_id_defaults_to_lowercased_github(self):
        p = config.parse('[[people]]\ngithub = "SimonW"')["people"][0]
        self.assertEqual(p["id"], "simonw")
        self.assertEqual(p["name"], "SimonW")

    def test_youtube_only_person_needs_an_id(self):
        with self.assertRaisesRegex(config.ConfigError, "github username or an id"):
            config.parse('[[people]]\nyoutube_channel_id = "UC2ojq-nuP8ceeHqiroeKhBA"')

    def test_needs_something_to_follow(self):
        with self.assertRaisesRegex(config.ConfigError, "to follow"):
            config.parse('[[people]]\nid = "nobody"')

    def test_rejects_bad_values(self):
        cases = {
            '[[people]]\ngithub = "not valid!"': "GitHub username",
            '[[people]]\nid = "Bad Id"\ngithub = "x"': "lowercase",
            '[[people]]\nid = "x"\nyoutube_channel_id = "nope"': "channel id",
            '[[people]]\ngithub = "x"\nlinks = [{ label = "a", url = "javascript:alert(1)" }]': "http",
            '[[people]]\ngithub = "x"\navatar = "data:image/png;base64,AAAA"': "http",
            '[[people]]\ngithub = "x"\nname = "a\\nb"': "control",
            '[[people]]\ngithub = "x"\n[[people]]\ngithub = "X"': "twice",
            '[site]\nmode = "secret"': "public",
            '[writer]\nkind = "gpt"': "writer",
            "not toml = = =": "TOML",
        }
        for text, msg in cases.items():
            with self.subTest(text=text), self.assertRaisesRegex(config.ConfigError, msg):
                config.parse(text)


class Environment(unittest.TestCase):
    def test_workflow_overrides_url_and_base_path(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "watchlist.toml").write_text('[site]\nurl = "https://old.example"')
            with mock.patch.dict(os.environ, {"PR_POST_SITE_URL": "https://me.github.io/pr-post/", "PR_POST_BASE_PATH": "/pr-post/"}):
                site = config.load(d)["site"]
        self.assertEqual(site["url"], "https://me.github.io/pr-post")
        self.assertEqual(site["base_path"], "/pr-post")

    def test_root_base_path_means_none(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "watchlist.toml").write_text("")
            with mock.patch.dict(os.environ, {"PR_POST_BASE_PATH": "/"}):
                self.assertEqual(config.load(d)["site"]["base_path"], "")

    def test_paper_dir_prefers_argument_then_env(self):
        with mock.patch.dict(os.environ, {"PR_POST_PAPER": "/tmp/env-paper"}):
            self.assertEqual(config.paper_dir("/tmp/arg-paper"), Path("/tmp/arg-paper"))
            self.assertEqual(config.paper_dir(), Path("/tmp/env-paper"))


if __name__ == "__main__":
    unittest.main()
