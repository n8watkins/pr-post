"""The Gemini writer (writers/gemini) and the compare tools (tools/compare), offline.

Every person is invented (tests/gemini_fixture.py). The Gemini API is a local
HTTP server that answers like generateContent; no test reaches the network.
"""

import datetime as dt
import http.server
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))

import gemini_fixture  # noqa: E402
import store  # noqa: E402
from tools.compare import blind  # noqa: E402
from tools.compare import __main__ as compare_cli  # noqa: E402
from writers.gemini import __main__ as cli  # noqa: E402
from writers.gemini import api, checks, fake, pipeline, prepare  # noqa: E402

SATURDAY = dt.date(2026, 11, 14)
WEDNESDAY = dt.date(2026, 11, 11)
KEY = "k" * 39


def end_of(day):
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.timezone.utc)


class MockGemini:
    """A local generateContent endpoint. `responder(system, prompt, schema, n)` returns (status, body dict)."""

    def __init__(self, responder=None):
        self.requests = []
        self.responder = responder or self.fake_answer
        mock = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.requests.append({"path": self.path, "key": self.headers.get("x-goog-api-key"), "body": body})
                system = body["systemInstruction"]["parts"][0]["text"]
                prompt = body["contents"][0]["parts"][0]["text"]
                schema = body["generationConfig"]["responseSchema"]
                status, out = mock.responder(system, prompt, schema, len(mock.requests))
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1beta"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def wrap(answer, tokens=(100, 20)):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": tokens[0], "candidatesTokenCount": tokens[1], "thoughtsTokenCount": 5}}

    def fake_answer(self, system, prompt, schema, n):
        return 200, self.wrap(fake.respond(system, prompt, schema))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FixtureCase(unittest.TestCase):
    kind = "weekly"
    day = SATURDAY

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paper = gemini_fixture.write_paper(Path(self.tmp.name) / "paper", self.day, self.kind)

    def tearDown(self):
        self.tmp.cleanup()

    def prep(self, kind=None):
        return prepare.prepare(self.paper, kind or self.kind, self.day, end_of(self.day))

    def person(self, prep, pid):
        return next(p for p in prep["people"] if p["id"] == pid)


class WindowTest(unittest.TestCase):
    def test_weekly_runs_from_the_newest_weekly_and_update_from_the_newest_report(self):
        reports = [("2026-11-11", "update"), ("2026-11-07", "weekly")]
        self.assertEqual(prepare.window("weekly", SATURDAY, reports), (dt.date(2026, 11, 7), SATURDAY))
        self.assertEqual(prepare.window("update", SATURDAY, reports), (dt.date(2026, 11, 11), SATURDAY))

    def test_fallbacks_are_seven_and_four_days(self):
        self.assertEqual(prepare.window("weekly", SATURDAY, [])[0], dt.date(2026, 11, 7))
        self.assertEqual(prepare.window("update", WEDNESDAY, [])[0], dt.date(2026, 11, 7))

    def test_the_brief_fills_in_when_reports_were_not_pulled(self):
        brief = "Newest report: 2026-11-11-update; newest weekly: 2026-11-07-weekly.\n"
        self.assertEqual(prepare.window("update", SATURDAY, [], brief)[0], dt.date(2026, 11, 11))
        self.assertEqual(prepare.window("weekly", SATURDAY, [], brief)[0], dt.date(2026, 11, 7))

    def test_a_report_from_today_does_not_move_the_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "reports" / "ada"
            folder.mkdir(parents=True)
            (folder / "2026-11-07-weekly.md").write_text("x")
            (folder / "2026-11-14-weekly.md").write_text("Claude already wrote today's")
            self.assertEqual(prepare.report_dates(tmp, "ada", SATURDAY), [("2026-11-07", "weekly")])

    def test_kind_follows_the_day(self):
        self.assertEqual(prepare.kind_for(SATURDAY), "weekly")
        self.assertEqual(prepare.kind_for(WEDNESDAY), "update")


class TrimTest(unittest.TestCase):
    BRIEF = """# Brief: Ada (ada)

Collected 2026-11-14T06:00:00+00:00; data covers items since 2026-10-20.

## Numbers (copied from stats/)
- 3 merged PRs, 4 opened, 9 commits, 1 repos touched, 1 releases, 0 videos.

## Merged PRs, newest first (3)
- 2026-11-13T10:00:00Z ada/tool#12 Fix the parser <https://github.com/ada/tool/pull/12>
  Long description of the fix.
- 2026-11-01T10:00:00Z ada/tool#11 Old work <https://github.com/ada/tool/pull/11>
  Old description.
- plus 4 more in data/ada.json

## Releases
- 2026-10-30 ada/tool v0.9 <https://github.com/ada/tool/releases/tag/v0.9>

## New repos
- 2026-11-12 ada/fresh: Something new <https://github.com/ada/fresh>
"""

    def test_items_outside_the_window_go_with_their_descriptions(self):
        text, items = prepare.trim(self.BRIEF, dt.date(2026, 11, 7), SATURDAY)
        self.assertIn("[i1] 2026-11-13T10:00:00Z ada/tool#12 Fix the parser", text)
        self.assertIn("Long description of the fix.", text)
        self.assertNotIn("Old work", text)
        self.assertNotIn("Old description", text)
        self.assertNotIn("plus 4 more", text)  # an item was cut, so the rest are older still
        self.assertNotIn("## Releases", text)  # an emptied section disappears
        self.assertIn("## Merged PRs in the window, newest first\n", text)  # no stale count
        self.assertIn("## Numbers (copied from stats/)", text)
        self.assertEqual([(i["id"], i["kind"], i["repo"], i["title"]) for i in items],
                         [("i1", "pr", "ada/tool", "Fix the parser"), ("i2", "birth", "ada/fresh", "Something new")])

    def test_the_window_is_inclusive(self):
        _, items = prepare.trim(self.BRIEF, dt.date(2026, 11, 1), dt.date(2026, 11, 13))
        self.assertEqual([i["title"] for i in items], ["Fix the parser", "Old work", "Something new"])


class PrepareTest(FixtureCase):
    def test_quiet_people_are_detected_and_get_no_request(self):
        prep = self.prep()
        status = {p["id"]: p["status"] for p in prep["people"]}
        self.assertEqual(sorted(pid for pid, s in status.items() if s == "quiet"), ["oda-fixture", "pim-fixture", "quin-fixture"])
        self.assertEqual(sum(1 for s in status.values() if s == "active"), 16)
        self.assertEqual(self.person(prep, "ada-fixture")["profile"].split("\n")[0], "# Ada Fixture")
        self.assertNotIn("profile", self.person(prep, "oda-fixture"))  # a quiet person's profile is not read

    def test_a_stale_or_missing_brief_is_skipped(self):
        (self.paper / "briefs" / "bram-fixture.md").unlink()
        prep = prepare.prepare(self.paper, "weekly", SATURDAY, end_of(SATURDAY) + dt.timedelta(days=1))
        self.assertEqual(self.person(prep, "bram-fixture")["why"], "no brief")
        self.assertEqual(self.person(prep, "ada-fixture")["why"], "brief older than 24 hours")

    def test_request_count_for_the_19_person_fixture(self):
        prompts = pipeline.load_prompts()
        weekly = pipeline.plan(prompts, self.prep(), "split", None)
        self.assertEqual(len(weekly), 17)  # 16 active people and one edition
        self.assertEqual(len(pipeline.plan(prompts, self.prep(), "single", None)), 1)
        self.assertTrue(all(r["est_input_tokens"] > 0 for r in weekly))

    def test_midweek_request_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            paper = gemini_fixture.write_paper(Path(tmp) / "p", WEDNESDAY, "update")
            prep = prepare.prepare(paper, None, WEDNESDAY, end_of(WEDNESDAY))
            self.assertEqual(prep["kind"], "update")
            self.assertEqual(len(pipeline.plan(pipeline.load_prompts(), prep, "split", None)), 15)  # 14 active and one edition

    def test_dry_run_prints_the_plan_and_sends_nothing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["run", "--paper", str(self.paper), "--date", SATURDAY.isoformat(), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Dry run: 17 requests planned (16 person, 1 edition, 0 single)", out.getvalue())
        self.assertNotIn("fixture", out.getvalue())  # counts only: no people in public logs
        self.assertFalse((self.paper / "gemini").exists())


class ChecksTest(unittest.TestCase):
    def test_numbers_must_come_from_the_input(self):
        given = "- 5 merged PRs, 12 commits. Released v1.4.0 on 2026-11-07 <https://github.com/a/b/pull/42>"
        self.assertEqual(checks.untraceable_numbers("Five PRs: 5 merged, PR #42, v1.4.0, on Nov 7.", given), [])
        self.assertEqual(checks.untraceable_numbers("17 commits and 5 PRs", given), ["17"])
        self.assertEqual(checks.untraceable_numbers("[the fix](https://github.com/a/b/pull/999)", given), [])  # links are checked separately
        self.assertEqual(checks.unknown_urls("[x](https://github.com/a/b/pull/999)", given), ["https://github.com/a/b/pull/999"])

    def test_word_caps_ignore_link_targets(self):
        self.assertEqual(checks.words("Shipped [the parser](https://github.com/a/b/pull/1) today."), 4)
        problems = checks.check_text("report_md", "word " * 121, "", checks.CAPS["update"])
        self.assertEqual(problems, ["report_md has 121 words; the limit is 120"])

    def test_a_quoted_description_is_not_the_writer_saying_you(self):
        brief = "- 2026-11-10 o/bank: A tiny currency for your Slack <https://github.com/o/bank>"
        self.assertFalse(checks.second_person("Dax started bank, a tiny currency for your Slack.", brief))
        self.assertTrue(checks.second_person("Dax started bank for your team.", brief))
        self.assertTrue(checks.second_person("You shipped it."))

    def test_copied_runs(self):
        source = "Released Datasette 1.0a42 with a redesigned homepage showing a searchable catalog of tables."
        self.assertTrue(checks.copied_runs("Simon released Datasette 1.0a42 with a redesigned homepage showing a searchable catalog.", source))
        self.assertEqual(checks.copied_runs("Simon's Datasette got a new front page you can search.", source), [])

    def test_shape(self):
        schema = pipeline.edition_schema()
        self.assertIn("answer is missing weather", checks.check_shape({"tldr": ["a", "b", "c"], "lead": {}, "corrections": []}, schema))
        self.assertIn("answer.tldr needs at least 3 items", checks.check_shape({"tldr": ["a"], "weather": "", "corrections": [],
                                                                               "lead": {"headline": "", "deck": "", "body": ["x"]}}, schema))

    def test_em_dashes_are_replaced(self):
        self.assertEqual(checks.no_em_dash({"a": ["one \u2014 two", "x\u2014y"]}), {"a": ["one - two", "x-y"]})


class ApiTest(unittest.TestCase):
    def test_sends_the_key_in_a_header_and_reads_usage(self):
        mock = MockGemini(lambda s, p, sc, n: (200, MockGemini.wrap({"ok": True}, (321, 45))))
        self.addCleanup(mock.close)
        client = api.Client("gemini-test", api_key=KEY, base=mock.base)
        text, usage, info = client.generate("sys", "hello", {"type": "OBJECT"})
        self.assertEqual(json.loads(text), {"ok": True})
        self.assertEqual(usage, {"input_tokens": 321, "output_tokens": 50, "thinking_tokens": 5})
        req = mock.requests[0]
        self.assertEqual(req["path"], "/v1beta/models/gemini-test:generateContent")
        self.assertEqual(req["key"], KEY)
        self.assertNotIn(KEY, json.dumps(req["body"]))  # never in the body or the URL
        self.assertEqual(req["body"]["generationConfig"]["responseMimeType"], "application/json")

    def test_rate_limits_are_retried_after_the_servers_delay(self):
        slept = []

        def responder(s, p, sc, n):
            if n == 1:
                return 429, {"error": {"code": 429, "message": "slow down", "details": [{"retryDelay": "7s"}]}}
            return 200, MockGemini.wrap({"ok": True})
        mock = MockGemini(responder)
        self.addCleanup(mock.close)
        _, _, info = api.Client("m", api_key=KEY, base=mock.base, sleep=slept.append).generate("s", "p", {})
        self.assertEqual((slept, info["transport_retries"]), ([7.0], 1))

    def test_errors_do_not_carry_the_key(self):
        mock = MockGemini(lambda s, p, sc, n: (400, {"error": {"message": "API key not valid"}}))
        self.addCleanup(mock.close)
        with self.assertRaises(api.GeminiError) as cm:
            api.Client("m", api_key=KEY, base=mock.base).generate("s", "p", {})
        self.assertIn("400", str(cm.exception))
        self.assertNotIn(KEY, str(cm.exception))

    def test_no_key_is_a_clear_error(self):
        with self.assertRaisesRegex(api.GeminiError, "GEMINI_API_KEY"):
            api.Client("m", api_key="")


class PipelineTest(FixtureCase):
    def run_split(self, responder=None, prep=None):
        mock = MockGemini(responder)
        self.addCleanup(mock.close)
        runner = pipeline.Runner(api.Client("m", api_key=KEY, base=mock.base), pipeline.load_prompts())
        prep = prep or self.prep()
        answers, front, lead_id, failed = pipeline.run_split(runner, prep, None)
        return mock, runner, prep, answers, front, lead_id, failed

    def test_one_request_per_active_person_then_one_edition_that_sees_no_briefs(self):
        mock, runner, prep, answers, front, lead_id, failed = self.run_split()
        self.assertEqual(len(mock.requests), 17)
        self.assertEqual(failed, [])
        edition_prompt = mock.requests[-1]["body"]["contents"][0]["parts"][0]["text"]
        self.assertNotIn("## Numbers (copied from stats/)", edition_prompt)  # stage 2 sees stage 1's answers only
        self.assertIn(answers[lead_id]["report_full"].strip(), edition_prompt)
        self.assertEqual(sum(c["input_tokens"] for c in runner.calls), 1700)

    def test_a_failed_check_is_retried_once_with_the_problems(self):
        def responder(system, prompt, schema, n):
            answer = fake.respond(system, prompt, schema)
            if "report_md" in schema["properties"] and "failed these checks" not in prompt:
                answer["report_md"] += "\n- It merged 4817 PRs.\n"
            return 200, MockGemini.wrap(answer)
        mock, runner, prep, answers, front, lead_id, failed = self.run_split(responder)
        self.assertEqual(len(mock.requests), 16 * 2 + 1)
        retry = mock.requests[1]["body"]["contents"][0]["parts"][0]["text"]
        self.assertIn("prints numbers that are not in the input: 4817", retry)
        person_calls = [c for c in runner.calls if c["stage"] == "person"]
        self.assertTrue(all(c["attempts"] == 2 and c["problems_final"] == [] for c in person_calls))
        self.assertNotIn("4817", "".join(a["report_full"] for a in answers.values()))

    def test_a_second_failure_is_kept_and_logged(self):
        def responder(system, prompt, schema, n):
            answer = fake.respond(system, prompt, schema)
            if "report_md" in schema["properties"]:
                answer["report_md"] += "\n- word" * 300
            return 200, MockGemini.wrap(answer)
        _, runner, *_ = self.run_split(responder)
        call = next(c for c in runner.calls if c["stage"] == "person")
        self.assertEqual(call["attempts"], 2)
        self.assertRegex(call["problems_final"][0], r"report_md has \d+ words; the limit is 250")

    def test_word_cap_for_the_front_page(self):
        def responder(system, prompt, schema, n):
            answer = fake.respond(system, prompt, schema)
            if "tldr" in schema["properties"] and "failed these checks" not in prompt:
                answer["lead"]["body"] = ["word " * 401]
            return 200, MockGemini.wrap(answer)
        _, runner, *_ = self.run_split(responder)
        edition_call = runner.calls[-1]
        self.assertRegex(edition_call["problems_first"][0], r"has \d+ words; the limit is 400")
        self.assertEqual(edition_call["problems_final"], [])

    def test_the_quote_is_copied_from_the_data_by_id(self):
        prep = self.prep()
        ada = self.person(prep, "ada-fixture")
        pr = next(i for i in ada["items"] if i["kind"] == "pr")
        ada["titles"][pr["url"]] = "The exact title from data, longer than the brief showed"
        release = next(i for i in ada["items"] if i["kind"] == "release")
        answers = {"ada-fixture": {"quote_candidate_ids": ["i999", release["id"], pr["id"]]}}
        quote = pipeline.pick_quote(prep, answers, ["ada-fixture"])
        self.assertEqual(quote["text"], "The exact title from data, longer than the brief showed")  # an unknown id and a release are passed over
        self.assertEqual((quote["url"], quote["by"], quote["where"]), (pr["url"], "Ada Fixture", "tool-1"))

    def test_a_shortened_brief_title_is_never_quoted(self):
        prep = self.prep()
        ada = self.person(prep, "ada-fixture")
        ada["titles"] = {}
        ada["items"][0]["title"] = "A title the brief cut short..."
        self.assertIsNone(pipeline.pick_quote(prep, {"ada-fixture": {"quote_candidate_ids": [ada["items"][0]["id"]]}}, ["ada-fixture"]))


class NewsValueTest(unittest.TestCase):
    def item(self, title, kind="pr", repo="o/r", date="2026-11-10"):
        return {"id": "i1", "kind": kind, "repo": repo, "title": title, "url": f"https://github.com/{repo}/pull/1", "date": date}

    def test_strong_titles_beat_routine_ones(self):
        score = lambda t: prepare.news_score(self.item(t), [])
        self.assertGreater(score("1.46x faster tinygrad, mergable"), score("fix: a typo in the help text"))
        self.assertGreater(score("Release prep: Hex 3.4.0 version and notes"), score("split control settings keys out of settings.rs"))
        self.assertGreater(score("libghostty-vt: prepare for 1.0.0"), score("chore: bump deps"))

    def test_launch_means_a_launch_not_app_start(self):
        self.assertLess(prepare.news_score(self.item("fix(settings): publish saved settings at launch"), []),
                        prepare.news_score(self.item("Launch the public beta"), []))

    def test_highlights_lead_with_new_repos_and_releases_and_end_with_archives(self):
        items = [self.item("chore: bump deps"), self.item("A tiny new tool", kind="birth", repo="o/new"),
                 self.item("v2.0.0", kind="release", repo="o/r"), self.item("v1.9.0", kind="release", repo="o/r")]
        gone = [{"repo": "o/old", "url": "https://github.com/o/old"}]
        out = prepare.highlights(items, gone, [])
        self.assertEqual([h["kind"] for h in out], ["release", "birth", "pr", "archived"])  # a 2.0 beats a new repo; the chore comes last
        self.assertEqual(out[0]["title"], "v2.0.0")  # one entry per repo's releases, the newest


class V2ChecksTest(FixtureCase):
    kind = "update"
    day = WEDNESDAY

    def setUp(self):
        super().setUp()
        self.p = self.prep()
        self.other = self.person(self.p, "ada-fixture")
        self.reader = self.person(self.p, "sol-fixture")
        self.top = self.other["highlights"][0]

    def answer(self, body):
        return {"report_md": body, "story": {"beat": "Tools desk", "headline": "Ada ships", "summary": "Ada had a week."}}

    def problems(self, person, body):
        return pipeline.check_person(self.answer(body), person, "update", person["brief"])

    def good(self, who):
        return (f"- {who} shipped the thing everyone waited for: [{self.top['title']}]({self.top['url']}).\n"
                f"- {who} also kept the project tidy this week: [{self.top['title']}]({self.top['url']}).\n")

    def test_a_good_report_passes(self):
        self.assertEqual(self.problems(self.other, self.good("Ada")), [])

    def test_you_only_for_the_reader(self):
        self.assertTrue(any("is not the reader" in p for p in self.problems(self.other, self.good("You"))))
        reader_top = self.reader["highlights"][0]
        body = f"- Sol shipped a new thing for the paper: [{reader_top['title']}]({reader_top['url']}).\n- Sol kept going on it: [x]({reader_top['url']}).\n"
        self.assertTrue(any("second person" in p for p in self.problems(self.reader, body)))

    def test_links_must_come_from_this_persons_brief(self):
        stranger = self.person(self.p, "bram-fixture")["items"][0]["url"]
        body = self.good("Ada") + f"- Ada also looked at a friend's repo: [theirs]({stranger}).\n"
        self.assertTrue(any("not items of this person's brief" in p for p in pipeline.check_person(
            self.answer(body), self.other, "update", self.other["brief"] + "\n" + stranger)))

    def test_quiet_is_wrong_when_the_brief_has_news(self):
        self.assertGreaterEqual(self.top["score"], pipeline.NEWS_SCORE)
        self.assertTrue(any("says quiet" in p for p in self.problems(self.other, "Quiet since Nov 7.")))

    def test_a_bullet_that_is_only_a_linked_title_fails(self):
        body = f"- [{self.top['title']}]({self.top['url']})\n" + self.good("Ada")
        self.assertTrue(any("only a linked title" in p for p in self.problems(self.other, body)))

    def test_a_report_without_the_top_news_fails(self):
        minor = [i for i in self.other["items"] if not any(pipeline.covers(h, [i["url"]]) for h in self.other["highlights"][:3])]
        if not minor:
            self.skipTest("every fixture item is a top item")
        body = "".join(f"- Ada kept the project moving along nicely: [{i['title']}]({i['url']}).\n" for i in minor[:2])
        self.assertTrue(any("biggest news" in p for p in self.problems(self.other, body)))

    def test_link_labels_are_short_and_release_bullets_need_no_more(self):
        long = f"- Ada made the build faster for everyone: [{'a long pasted pull request title ' * 2}]({self.top['url']}).\n"
        self.assertTrue(any("link labels must be short" in p for p in self.problems(self.other, long + self.good("Ada"))))
        self.assertTrue(pipeline.release_only("Also released [tool v1.2.0](https://github.com/o/tool/releases/tag/v1.2.0)."))
        self.assertFalse(pipeline.release_only("[PR 3](https://github.com/o/tool/pull/3)"))

    def test_the_front_page_has_no_links_and_does_not_copy_the_lead(self):
        report = "- Ada shipped the long awaited sync engine that keeps every laptop in step: [PR 1](https://x/pull/1)."
        front = {"tldr": ["Ada shipped [sync](https://x/pull/1)."], "weather": "Clear.", "corrections": ["None."],
                 "lead": {"headline": "Sync", "deck": "Ada shipped.", "body": ["Ada shipped the long awaited sync engine that keeps every laptop in step."]}}
        problems = pipeline.check_edition(front, report, report)
        self.assertTrue(any("no Markdown links" in p for p in problems))
        self.assertTrue(any("word for word" in p for p in problems))

    def test_an_archived_repo_bullet_needs_no_more_words(self):
        gone = self.other["archived"] or [{"url": "https://github.com/ada-fixture/old"}]
        self.assertTrue(pipeline.release_only(f"Ada archived [old]({gone[0]['url']}).", [g["url"] for g in gone]))

    def test_the_fallback_uses_only_the_brief(self):
        fb = pipeline.fallback_answer(self.other, "update")
        self.assertTrue(fb["fallback"])
        urls = set(checks.URL_RE.findall(fb["report_md"]))
        self.assertTrue(urls and urls <= pipeline.brief_urls(self.other))
        self.assertFalse(checks.second_person(fb["report_md"]))
        self.assertEqual(pipeline.hard_problems(pipeline.check_person(fb, self.other, "update", self.other["brief"])), [])

    def test_births_and_obituaries_come_from_every_brief(self):
        births, obits = pipeline.births_and_obituaries(self.p)
        want_b = {i["repo"] for p in self.p["people"] for i in p["items"] if i["kind"] == "birth"}
        want_o = {g["repo"].split("/")[-1] for p in self.p["people"] for g in p["archived"]}
        self.assertEqual({b["repo"] for b in births}, want_b)
        self.assertEqual({o["name"] for o in obits}, want_o)
        self.assertTrue(want_b and want_o)
        self.assertTrue(all(not b["text"].endswith("..") or b["text"].endswith("...") for b in births))
        self.assertNotIn("....", "".join(b["text"] for b in births))


class FallbackRunTest(FixtureCase):
    kind = "update"
    day = WEDNESDAY
    run_split = PipelineTest.run_split

    def test_a_wrong_you_twice_falls_back_to_a_report_from_the_brief(self):
        def responder(system, prompt, schema, n):
            answer = fake.respond(system, prompt, schema)
            if "report_md" in schema["properties"] and "Ada Fixture (id: ada-fixture)" in prompt:
                answer["report_md"] = answer["report_md"].replace("Ada Fixture", "You")
            return 200, MockGemini.wrap(answer)
        mock, runner, prep, answers, front, lead_id, failed = self.run_split(responder)
        self.assertEqual(failed, [])
        ada = answers["ada-fixture"]
        self.assertTrue(ada.get("fallback"))
        self.assertFalse(checks.second_person(ada["report_md"]))
        self.assertTrue(next(c for c in runner.calls if c["id"] == "ada-fixture")["fallback"])

    def test_single_mode_fills_a_missing_person_from_the_brief(self):
        mock = MockGemini()
        self.addCleanup(mock.close)
        def responder(system, prompt, schema, n):
            answer = fake.respond(system, prompt, schema)
            answer["people"] = [a for a in answer["people"] if a["id"] != "ada-fixture"]
            return 200, MockGemini.wrap(answer)
        mock.responder = responder
        runner = pipeline.Runner(api.Client("m", api_key=KEY, base=mock.base), pipeline.load_prompts())
        answers, front, lead_id, failed = pipeline.run_single(runner, self.prep(), None)
        self.assertEqual(failed, [])
        self.assertTrue(answers["ada-fixture"]["fallback"])
        self.assertIn("ada-fixture", runner.calls[-1]["fallback_ids"])


class CliTest(FixtureCase):
    def run_cli(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["run", "--paper", str(self.paper), "--date", SATURDAY.isoformat(), "--no-push", *extra])
        return code, out.getvalue()

    def test_mock_run_writes_only_under_gemini(self):
        before = sorted(p.relative_to(self.paper).as_posix() for p in self.paper.rglob("*") if p.is_file())
        code, out = self.run_cli("--mock")
        self.assertEqual(code, 0, out)
        after = sorted(p.relative_to(self.paper).as_posix() for p in self.paper.rglob("*") if p.is_file())
        self.assertTrue(all(p.startswith("gemini/") for p in set(after) - set(before)))
        self.assertEqual(len([p for p in after if p.startswith("gemini/reports/")]), 19)
        self.assertIn("gemini/profiles/finn-fixture.md", after)  # no profile yet, so the writer drafted one
        self.assertNotIn("profiles/finn-fixture.md", after)  # and Claude's profiles are untouched
        edition = json.loads((self.paper / "gemini" / "editions" / "2026-11-14-weekly.json").read_text())
        self.assertEqual((edition["source"], edition["prompt_version"], edition["mode"]), ("mock", pipeline.PROMPT_VERSION, "split"))
        self.assertEqual(len(edition["stories"]), 18)  # everyone but the lead, quiet people included
        self.assertEqual([b["repo"] for b in edition["births"]], ["esme-fixture/fresh-idea"])
        self.assertEqual([o["name"] for o in edition["obituaries"]], ["old-thing"])
        quiet = (self.paper / "gemini" / "reports" / "oda-fixture" / "2026-11-14-weekly.md").read_text()
        self.assertEqual(quiet, "# Oda Fixture: Nov 7 to Nov 14\n\nQuiet since Nov 7, 2026.\n")
        usage = json.loads((self.paper / "gemini" / "usage" / "2026-11-14.json").read_text())
        self.assertEqual((usage["requests"], usage["retries"], usage["people"]["quiet"]), (17, 0, 3))
        self.assertNotIn("fixture", out)

    def test_single_mode_saves_beside_the_split_run(self):
        code, out = self.run_cli("--mock", "--single")
        self.assertEqual(code, 0, out)
        usage = json.loads((self.paper / "gemini" / "single" / "usage" / "2026-11-14.json").read_text())
        self.assertEqual((usage["requests"], usage["mode"]), (1, "single"))
        self.assertTrue((self.paper / "gemini" / "single" / "editions" / "2026-11-14-weekly.json").is_file())
        self.assertFalse((self.paper / "gemini" / "editions").exists())

    def test_real_client_against_the_mock_endpoint(self):
        mock = MockGemini()
        self.addCleanup(mock.close)
        env = {"GEMINI_API_KEY": KEY, "GEMINI_API_BASE": mock.base}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            code, out = self.run_cli("--rpm", "0")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(code, 0, out)
        self.assertEqual(len(mock.requests), 17)
        self.assertIn("Requests 17 (0 retries", out)
        self.assertNotIn(KEY, out)


class CompareTest(FixtureCase):
    def setUp(self):
        super().setUp()
        with redirect_stdout(io.StringIO()):
            cli.main(["run", "--paper", str(self.paper), "--date", SATURDAY.isoformat(), "--no-push", "--mock"])
        # Stand-in "Claude" output for the same run, at the paper's own reports/ and editions/.
        for pid in ("ada-fixture", "oda-fixture"):
            (self.paper / "reports" / pid / "2026-11-14-weekly.md").write_text(f"# {pid}: Nov 7 to Nov 14\n\n- Written by the other desk.\n")
        (self.paper / "editions").mkdir()
        (self.paper / "editions" / "2026-11-14-weekly.json").write_text(json.dumps(
            {"date": "2026-11-14", "kind": "weekly", "source": "claude", "tldr": ["Other desk."], "lead": {"id": "ada-fixture"}}))

    def test_the_packet_is_blind_and_the_key_maps_it(self):
        out, key = self.paper / ".compare" / "x", self.paper / ".compare" / "x.key.json"
        mapping = blind.build_packet(self.paper, "2026-11-14", "weekly", "", "gemini", out, key, random.Random(3))
        packet = (out / "packet.md").read_text()
        for word in ("claude", "gemini", "mock", "prompt_version", "prompt_sha", '"mode"', '"source"'):
            self.assertNotIn(word, packet.lower())
        self.assertEqual(json.loads(key.read_text()), mapping)
        self.assertEqual({mapping["A"]["root"], mapping["B"]["root"]}, {"", "gemini"})
        self.assertFalse(key.is_relative_to(out))  # the key is not in the folder the reviewer reads
        other = "A" if mapping["A"]["root"] == "" else "B"
        self.assertIn(f"#### Writer {other}: report\n\n~~~~markdown\n# ada-fixture: Nov 7 to Nov 14", packet)
        self.assertIn("[i1]", packet)  # the briefs, trimmed as the Gemini writer saw them

    def test_labels_are_shuffled(self):
        roots = set()
        for seed in range(12):
            with tempfile.TemporaryDirectory() as tmp:
                roots.add(blind.build_packet(self.paper, "2026-11-14", "weekly", "", "gemini", Path(tmp), Path(tmp) / "k.json", random.Random(seed))["A"]["root"])
        self.assertEqual(roots, {"", "gemini"})

    def test_unblind_names_the_writers_and_saves_the_review(self):
        with redirect_stdout(io.StringIO()):
            compare_cli.main(["packet", "--paper", str(self.paper), "--date", "2026-11-14", "--seed", "5"])
        key = json.loads((self.paper / ".compare" / "2026-11-14.key.json").read_text())
        review = self.paper / ".compare" / "2026-11-14" / "review.md"
        review.write_text("# Review\n\n## Verdict\nWriter A missed the release.\nWriter B was tighter.\n")
        with redirect_stdout(io.StringIO()):
            compare_cli.main(["unblind", "--paper", str(self.paper), "--date", "2026-11-14", "--price-in", "0.1", "--price-out", "0.4"])
        text = (self.paper / "reviews" / "2026-11-14.md").read_text()
        a, b = key["A"]["writer"], key["B"]["writer"]
        self.assertIn(f"Writer A was {a} and Writer B was {b}.", text)
        self.assertIn(f"{a} (Writer A) missed the release.", text)
        self.assertIn(f"{b} (Writer B) was tighter.", text)
        gemini_name = a if key["A"]["root"] == "gemini" else b
        self.assertIn(f"| {gemini_name} | 17 | 0 |", text)
        self.assertIn("| claude | not logged |", text)
        self.assertNotIn("\n# Review", text)

    def test_unblind_refuses_a_review_of_something_else(self):
        out, key = self.paper / ".compare" / "x", self.paper / ".compare" / "x.key.json"
        blind.build_packet(self.paper, "2026-11-14", "weekly", "", "gemini", out, key, random.Random(1))
        with self.assertRaisesRegex(ValueError, "never mentions Writer A"):
            blind.unblind(self.paper, key, "A review with no labels.")


@unittest.skipIf(shutil.which("node") is None, "needs Node for the Worker")
class StoreEndToEndTest(unittest.TestCase):
    """Pull with the jobs token, write with the mock model, push gemini/; the Claude routine's token cannot see it."""

    def setUp(self):
        seed = """INSERT INTO people (id, status, position, entry, profile) VALUES
          ('ada-fixture', 'active', 0, '{"github":"ada-fixture","id":"ada-fixture","name":"Ada Fixture"}', '# Ada Fixture\n');
          INSERT INTO settings (section, value) VALUES ('site', '{"title":"T","mode":"private"}');"""
        env = {**os.environ, "JOBS_TOKEN": "j" * 40, "WRITER_TOKEN": "w" * 40, "SITE_PASSWORD": "p" * 16, "SEED_SQL": seed}
        self.server = subprocess.Popen(["node", str(HERE / "serve_worker.mjs")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)
        line = self.server.stdout.readline()
        self.assertTrue(line.startswith("listening"), line)
        self.base = f"http://127.0.0.1:{line.split()[1]}"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.kill()
        self.server.wait()
        self.server.stdout.close()
        self.tmp.cleanup()

    def test_gemini_output_lands_in_the_paper(self):
        jobs = store.Api(self.base, "j" * 40)
        work = Path(self.tmp.name) / "work"
        said = []
        store.pull(jobs, work, say=said.append)
        source = gemini_fixture.write_paper(Path(self.tmp.name) / "src", SATURDAY)
        (work / "briefs").mkdir()
        shutil.copy(source / "briefs" / "ada-fixture.md", work / "briefs" / "ada-fixture.md")
        out = io.StringIO()
        old = {k: os.environ.get(k) for k in ("PR_POST_API", "PR_POST_TOKEN")}
        os.environ.update({"PR_POST_API": self.base, "PR_POST_TOKEN": "j" * 40})
        try:
            with redirect_stdout(out):
                code = cli.main(["run", "--paper", str(work), "--date", SATURDAY.isoformat(), "--mock"])
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("Pushed 3 files, 0 profiles", out.getvalue())  # a report, the edition and the usage log; no profile change

        fresh = Path(self.tmp.name) / "fresh"
        store.pull(jobs, fresh, say=said.append)
        self.assertTrue((fresh / "gemini" / "reports" / "ada-fixture" / "2026-11-14-weekly.md").is_file())
        self.assertFalse((fresh / "reports").exists())  # nothing written to Claude's reports
        writer = Path(self.tmp.name) / "writer"
        store.pull(store.Api(self.base, "w" * 40), writer, say=said.append)
        self.assertFalse((writer / "gemini").exists())


if __name__ == "__main__":
    unittest.main()
