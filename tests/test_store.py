"""scripts/store.py: watchlist.toml rendering, and pull/push/site against the real Worker code.

The end-to-end tests start tests/serve_worker.mjs (the Worker with D1 on
node:sqlite and an in-memory R2) and are skipped when Node is missing.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import config  # noqa: E402
import store  # noqa: E402

JOBS = "j" * 40
WRITER = "w" * 40


def split(toml_text):
    raw = tomllib.loads(toml_text)
    return {k: v for k, v in raw.items() if k != "people"}, raw.get("people", [])


class RenderTest(unittest.TestCase):
    def test_demo_watchlist_round_trips(self):
        text = (HERE.parent / "demo" / "watchlist.toml").read_text()
        settings, people = split(text)
        out = store.render_watchlist(settings, people)
        self.assertEqual(config.parse(out), config.parse(text))
        self.assertEqual(tomllib.loads(out), tomllib.loads(text))

    def test_nested_tables_quoted_keys_and_escapes(self):
        settings = {
            "site": {"title": 'The "Post"', "mode": "private", "show_private_counts": True},
            "follow": {"enabled": False, "picks": 5, "stack_repos": ["a/b"],
                       "weights": {"stack": 30, "activity": 15.5},
                       "interests": {"Building in public": ["build in public", "devlog"]}},
        }
        people = [
            {"github": "Ada", "id": "ada", "name": "Ada \\ Löv", "starred": True, "self": False, "orgs": [],
             "links": [{"label": "X", "url": "https://x.com/ada"}], "known_for": "line\nbreak"},
            {"id": "tuber", "name": "Tuber", "youtube_channel_id": "UC" + "a" * 22, "github": ""},
        ]
        out = store.render_watchlist(settings, people)
        self.assertEqual(tomllib.loads(out), {**settings, "people": [
            {"github": "Ada", "name": "Ada \\ Löv", "starred": True, "known_for": "line\nbreak",
             "links": [{"label": "X", "url": "https://x.com/ada"}]},
            {"id": "tuber", "name": "Tuber", "youtube_channel_id": "UC" + "a" * 22},
        ]})
        self.assertIn('[follow.interests]\n"Building in public" = ', out)

    def test_rejects_what_toml_cannot_hold(self):
        with self.assertRaises(store.StoreError):
            store.render_watchlist({"site": {"x": None}}, [])
        with self.assertRaises(store.StoreError):
            store.render_watchlist({"site": "not a table"}, [])


@unittest.skipUnless(shutil.which("node"), "needs node")
class EndToEndTest(unittest.TestCase):
    def setUp(self):
        seed = """INSERT INTO people (id, status, position, entry, profile) VALUES
          ('ada', 'active', 0, '{"github":"ada","name":"Ada","links":[{"label":"X","url":"https://x.com/ada"}]}', '# Ada\n'),
          ('bob', 'paused', 1, '{"github":"bob","name":"Bob"}', ''),
          ('cy', 'draft', 2, '{"github":"cy","name":"Cy"}', '');
          INSERT INTO settings (section, value) VALUES ('site', '{"title":"T","mode":"private"}'), ('follow', '{"enabled":false,"weights":{"stack":30}}');"""
        env = {**os.environ, "JOBS_TOKEN": JOBS, "WRITER_TOKEN": WRITER, "SITE_PASSWORD": "p" * 16, "SEED_SQL": seed}
        self.server = subprocess.Popen(["node", str(HERE / "serve_worker.mjs")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
        line = self.server.stdout.readline()
        self.assertTrue(line.startswith("listening"), line)
        self.base = f"http://127.0.0.1:{line.split()[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.said = []

    def tearDown(self):
        self.server.kill()
        self.server.wait()
        self.server.stdout.close()
        self.tmp.cleanup()

    def api(self, token=JOBS):
        return store.Api(self.base, token)

    def say(self, line):
        self.said.append(line)

    def test_jobs_cycle(self):
        api = self.api()
        work = self.root / "work"
        store.pull(api, work, say=self.say)
        cfg = config.load(work)
        self.assertEqual([p["id"] for p in cfg["people"]], ["ada"])  # paused people and drafts are not collected
        self.assertEqual(cfg["site"]["title"], "T")
        self.assertEqual((work / "profiles" / "ada.md").read_text(), "# Ada\n")
        self.assertIn("Pulled 1 people, 1 profiles, 0 files", self.said[-1])
        self.assertNotIn("ada", " ".join(self.said))  # logs carry counts, not people

        (work / "data").mkdir()
        (work / "data" / "ada.json").write_text('{"n": 1}')
        (work / "briefs").mkdir()
        (work / "briefs" / "ada.md").write_text("brief")
        (work / "profiles" / "ada.md").write_text("# Ada\n- more\n")
        (work / "follow" / "people").mkdir(parents=True)
        (work / "follow" / "people" / "zed.json").write_text("{}")
        (work / "follow" / "state.json").write_text(json.dumps({"version": 1, "people": {"zed": {"status": "candidate"}, "amy": {"status": "followed"}}}))
        (work / "follow" / "actions.jsonl").write_text(json.dumps({"at": "2026-11-09T10:00:00+00:00", "action": "follow", "login": "zed",
                                                                    "nonce": "a" * 32, "result": "ok", "detail": ""}) + "\n")
        store.push(api, work, say=self.say)
        self.assertEqual(self.said[-1], "Pushed 3 files, 1 profiles, 2 follow candidates (0 removed), 1 logged actions.")

        store.push(api, work, say=self.say)  # nothing changed since
        self.assertEqual(self.said[-1], "Pushed 0 files, 0 profiles, 0 follow candidates (0 removed), 0 logged actions.")

        state = json.loads((work / "follow" / "state.json").read_text())
        state["people"]["zed"]["status"] = "suggested"
        del state["people"]["amy"]
        (work / "follow" / "state.json").write_text(json.dumps(state))
        store.push(api, work, say=self.say)
        self.assertEqual(self.said[-1], "Pushed 0 files, 0 profiles, 1 follow candidates (1 removed), 0 logged actions.")

        self.assertEqual(json.loads((work / "emailed.json").read_text()), [])
        self.assertEqual(json.loads((work / "suppressed.json").read_text()), [])
        (work / "emailed.json").write_text(json.dumps(["reports/ada/2026-11-09-weekly.md"]))
        store.push(api, work, say=self.say)
        self.assertEqual(self.said[-1], "Pushed 0 files, 0 profiles, 0 follow candidates (0 removed), 0 logged actions, 1 emailed reports.")

        fresh = self.root / "fresh"
        store.pull(api, fresh, say=self.say)
        self.assertEqual(json.loads((fresh / "emailed.json").read_text()), ["reports/ada/2026-11-09-weekly.md"])
        self.assertEqual((fresh / "data" / "ada.json").read_text(), '{"n": 1}')
        self.assertEqual((fresh / "profiles" / "ada.md").read_text(), "# Ada\n- more\n")
        self.assertEqual(json.loads((fresh / "follow" / "state.json").read_text()), {"version": 1, "people": {"zed": {"status": "suggested"}}})
        self.assertEqual(len((fresh / "follow" / "actions.jsonl").read_text().splitlines()), 1)
        self.assertTrue((fresh / "follow" / "people" / "zed.json").is_file())

        (fresh / "data" / "ada.json").unlink()
        store.push(api, fresh, dirs=("data",), delete=True, say=self.say)
        self.assertIn("(removed 1)", self.said[-1])

    def test_a_drafted_person_waits_for_the_owner(self):
        api = self.api()
        work = self.root / "work"
        store.pull(api, work, say=self.say)
        with open(work / "watchlist.toml", "a") as f:
            f.write('\n[[people]]\ngithub = "Dee"\nname = "Dee"\n')
        (work / "profiles" / "dee.md").write_text("# Dee\n## Still to check\n- socials\n")
        store.push(api, work, dirs=(), say=self.say)
        self.assertEqual(self.said[-1], "Pushed 0 files, 0 profiles, 0 follow candidates (0 removed), 0 logged actions, 1 new drafts.")
        _, wl = api.json("GET", "/api/paper/watchlist")
        self.assertEqual({p["id"]: p["status"] for p in wl["people"]}, {"ada": "active", "bob": "paused", "cy": "draft", "dee": "draft"})
        fresh = self.root / "fresh"
        store.pull(api, fresh, say=self.say)
        self.assertNotIn("Dee", (fresh / "watchlist.toml").read_text())  # drafts are not collected or written about
        (fresh / "discard.json").write_text('["dee"]')
        store.push(api, fresh, dirs=(), say=self.say)
        self.assertIn("1 drafts discarded", self.said[-1])

    def test_writer_lane(self):
        jobs = self.api()
        work = self.root / "work"
        store.pull(jobs, work, say=self.say)
        (work / "briefs").mkdir()
        (work / "briefs" / "ada.md").write_text("brief")
        (work / "logs").mkdir()
        (work / "logs" / "run.log").write_text("log")
        store.push(jobs, work, say=self.say)

        writer = self.api(WRITER)
        wwork = self.root / "writer"
        store.pull(writer, wwork, say=self.say)
        self.assertTrue((wwork / "briefs" / "ada.md").is_file())
        self.assertFalse((wwork / "logs").exists())
        self.assertFalse((wwork / "follow" / "state.json").exists())
        (wwork / "reports" / "ada").mkdir(parents=True)
        (wwork / "reports" / "ada" / "2026-11-09-weekly.md").write_text("report")
        (wwork / "profiles" / "ada.md").write_text("# Ada\n- 2026-11-09: new\n")
        store.push(writer, wwork, dirs=("reports", "editions"), say=self.say)
        self.assertEqual(self.said[-1], "Pushed 1 files, 1 profiles, 0 follow candidates (0 removed), 0 logged actions.")

        (wwork / "briefs" / "ada.md").write_text("tampered")
        with self.assertRaisesRegex(store.StoreError, "403"):
            store.push(writer, wwork, dirs=("briefs",), say=self.say)
        (wwork / "profiles" / "ada.md").write_text("rewritten")
        with self.assertRaisesRegex(store.StoreError, "403"):
            store.push(writer, wwork, dirs=("reports",), say=self.say)

    def test_site_publish_removes_stale_pages(self):
        api = self.api()
        dist = self.root / "dist"
        (dist / "p" / "ada").mkdir(parents=True)
        (dist / "index.html").write_text("front")
        (dist / "p" / "ada" / "index.html").write_text("ada")
        store.publish_site(api, dist, say=self.say)
        self.assertEqual(self.said[-1], "Site: 2 files, 2 uploaded, 0 removed.")
        shutil.rmtree(dist / "p")
        (dist / "index.html").write_text("front v2")
        store.publish_site(api, dist, say=self.say)
        self.assertEqual(self.said[-1], "Site: 1 files, 1 uploaded, 1 removed.")
        with self.assertRaisesRegex(store.StoreError, "403"):
            store.publish_site(self.api(WRITER), dist, say=self.say)

    def test_bad_token_and_missing_settings(self):
        with self.assertRaisesRegex(store.StoreError, "401"):
            store.pull(store.Api(self.base, "x" * 40), self.root / "w", say=self.say)
        with self.assertRaisesRegex(store.StoreError, "PR_POST_API"):
            store.Api("ftp://nope", JOBS)
        with self.assertRaisesRegex(store.StoreError, "not pulled"):
            store.push(self.api(), self.root, say=self.say)


if __name__ == "__main__":
    unittest.main()
