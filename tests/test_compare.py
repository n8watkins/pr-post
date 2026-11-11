"""The compare tools' scoring, Gemini reviewer and multi-review unblind, offline.

The packet comes from the invented paper in tests/gemini_fixture.py and the
Gemini API is the local mock endpoint from test_gemini.py; no test reaches the
network.
"""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock as umock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from test_gemini import KEY, FixtureCase, MockGemini  # noqa: E402
from tools.compare import __main__ as compare_cli  # noqa: E402
from tools.compare import blind, gemini_review, score  # noqa: E402
from writers.gemini import __main__ as writer_cli  # noqa: E402


def scores(missed=0, untraceable=0, numbers=0, caps=0, fmt=0, tone=0, readability=3, gut=5):
    return {"missed_news": [f"m{i}" for i in range(missed)], "untraceable_facts": [f"u{i}" for i in range(untraceable)],
            "wrong_numbers": [f"n{i}" for i in range(numbers)], "cap_overruns": [f"c{i}" for i in range(caps)],
            "format_errors": [f"f{i}" for i in range(fmt)], "tone_issues": [f"t{i}" for i in range(tone)],
            "readability": readability, "gut_score": gut}


def review_text(a, b, body="Writer A missed a release.\nWriter B was tighter."):
    block = json.dumps({"Writer A": a, "Writer B": b}, indent=1)
    return f"# Review\n\n## Verdict\n{body}\n\n## Scores\n\n```json\n{block}\n```\n"


class WeightsTest(unittest.TestCase):
    def test_the_documented_weights(self):
        self.assertEqual(score.BASE, 100)
        self.assertEqual(score.WEIGHTS, {"missed_news": -6, "untraceable_facts": -8, "wrong_numbers": -10, "cap_overruns": -2,
                                         "format_errors": -3, "tone_issues": -2, "readability": 4})
        docs = (HERE.parent / "docs" / "writers.md").read_text()
        for metric, weight in score.WEIGHTS.items():
            self.assertIn(f"| `{metric}` | {weight:+d}", docs)

    def test_points_apply_each_weight(self):
        parsed = score.parse_review(review_text(scores(2, 1, 1, 3, 1, 2, readability=4), scores(readability=5)))
        self.assertEqual(score.points(parsed["A"]), 100 - 12 - 8 - 10 - 6 - 3 - 4 + 16)
        self.assertEqual(score.points(parsed["B"]), 120)
        self.assertEqual(parsed["A"]["items"]["missed_news"], ["m0", "m1"])


class ParseTest(unittest.TestCase):
    def test_counts_may_be_numbers_and_keys_may_be_unblinded(self):
        raw = scores(readability=2)
        raw["missed_news"] = 3
        raw["tone_issues"] = {"count": 1, "items": ["x"]}
        text = "```json\n" + json.dumps({"claude (Writer A)": raw, "gemini (Writer B)": scores()}) + "\n```"
        parsed = score.parse_review(text)
        self.assertEqual((parsed["A"]["missed_news"], parsed["A"]["tone_issues"]), (3, 1))

    def test_the_last_scores_block_wins_and_other_json_is_ignored(self):
        text = '```json\n{"not": "scores"}\n```\n' + review_text(scores(1), scores()) + review_text(scores(4), scores())
        self.assertEqual(score.parse_review(text)["A"]["missed_news"], 4)

    def test_bad_blocks_are_refused(self):
        with self.assertRaisesRegex(ValueError, "no fenced JSON scores block"):
            score.parse_review("# Review\n\nWriter A, Writer B.")
        missing = scores()
        del missing["wrong_numbers"]
        with self.assertRaisesRegex(ValueError, "Writer A: the scores have no wrong_numbers"):
            score.parse_review(review_text(missing, scores()))
        with self.assertRaisesRegex(ValueError, "Writer B: readability"):
            score.parse_review(review_text(scores(), scores(readability=7)))
        with self.assertRaisesRegex(ValueError, "gut_score"):
            score.parse_review(review_text(scores(gut=11), scores()))
        bad = scores()
        bad["cap_overruns"] = True
        with self.assertRaisesRegex(ValueError, "cap_overruns must be a list"):
            score.parse_review(review_text(bad, scores()))


class RenderTest(unittest.TestCase):
    def test_two_reviewers_are_averaged_and_their_disagreement_shown(self):
        reviews = {"claude": score.parse_review(review_text(scores(1), scores(3))),
                   "gemini": score.parse_review(review_text(scores(3), scores(3)))}
        combined = score.combine(reviews)
        self.assertEqual(combined["A"]["points"], {"claude": 106, "gemini": 94})
        self.assertEqual(combined["A"]["mean"], 100)
        self.assertEqual(score.ranking(combined), ["A", "B"])
        out = score.render(reviews, {"A": "claude", "B": "gemini"})
        self.assertIn("| Writer | claude reviewer | gemini reviewer | Mean points | Gut score (mean) |", out)
        self.assertIn("| claude | 106 | 94 | 100 | 5 |", out)
        self.assertIn("| gemini | 94 | 94 | 94 | 5 |", out)
        self.assertIn("Ahead on points: claude.", out)
        self.assertIn("| missed_news | -6 | 1 | 3 | 2 | 3 | 3 | 0 |", out)

    def test_the_gut_score_breaks_a_tie(self):
        reviews = {"claude": score.parse_review(review_text(scores(gut=6), scores(gut=8)))}
        self.assertEqual(score.ranking(score.combine(reviews)), ["B", "A"])
        self.assertIn("Ahead on the gut score (points tied): Writer B.", score.render(reviews))
        tie = {"claude": score.parse_review(review_text(scores(), scores()))}
        self.assertIn("A tie on points and gut score.", score.render(tie))


def answer(a=None, b=None, md="# Review\n\n## Verdict\nWriter A is clearer than Writer B.\n\n## Scores\nleft over"):
    return {"review_md": md, "scores": {"A": a or scores(1, readability=4, gut=7), "B": b or scores(2, gut=6)}}


class CompareCase(FixtureCase):
    def setUp(self):
        super().setUp()
        with redirect_stdout(io.StringIO()):
            writer_cli.main(["run", "--paper", str(self.paper), "--date", "2026-11-14", "--no-push", "--mock"])
        for pid in ("ada-fixture", "oda-fixture"):
            (self.paper / "reports" / pid / "2026-11-14-weekly.md").write_text(f"# {pid}: Nov 7 to Nov 14\n\n- Written by the other desk.\n")
        (self.paper / "editions").mkdir()
        (self.paper / "editions" / "2026-11-14-weekly.json").write_text(json.dumps(
            {"date": "2026-11-14", "kind": "weekly", "source": "claude", "tldr": ["Other desk."], "lead": {"id": "ada-fixture"}}))
        self.folder = self.paper / ".compare" / "2026-11-14"
        self.cli("packet", "--date", "2026-11-14", "--seed", "5")
        self.key = json.loads((self.paper / ".compare" / "2026-11-14.key.json").read_text())

    def cli(self, *args, env=None):
        out = io.StringIO()
        with redirect_stdout(out), umock.patch.dict(os.environ, env or {}):
            code = compare_cli.main([args[0], "--paper", str(self.paper), *args[1:]])
        return code, out.getvalue()

    def gemini(self, responder):
        server = MockGemini(responder)
        self.addCleanup(server.close)
        return server, {"GEMINI_API_KEY": KEY, "GEMINI_API_BASE": server.base}


class GeminiReviewTest(CompareCase):
    def test_one_request_with_only_the_packet_and_the_reviewer_prompt(self):
        server, env = self.gemini(lambda s, p, sc, n: (200, MockGemini.wrap(answer())))
        code, out = self.cli("review", "--reviewer", "gemini", "--date", "2026-11-14", "--model", "gemini-test", env=env)
        self.assertEqual(code, 0)
        self.assertEqual(len(server.requests), 1)
        req = server.requests[0]
        self.assertEqual(req["path"], "/v1beta/models/gemini-test:generateContent")
        self.assertEqual(req["body"]["systemInstruction"]["parts"][0]["text"], (HERE.parent / "tools" / "compare" / "REVIEWER.md").read_text())
        prompt = req["body"]["contents"][0]["parts"][0]["text"]
        self.assertTrue(prompt.endswith((self.folder / "packet.md").read_text()))
        self.assertNotIn(self.key["created_at"], prompt)  # nothing from the key
        self.assertIn("Requests 1, 100 input and 25 output tokens", out)
        self.assertNotIn(KEY, out)
        text = (self.folder / "review-gemini.md").read_text()
        self.assertNotIn("left over", text)  # the model's own Scores section is replaced by ours
        parsed = score.parse_review(text)
        self.assertEqual((parsed["A"]["missed_news"], parsed["B"]["missed_news"], parsed["A"]["gut_score"]), (1, 2, 7))

    def test_an_unusable_answer_is_asked_once_more_then_given_up(self):
        def flaky(s, p, sc, n):
            return 200, ({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]} if n == 1 else MockGemini.wrap(answer()))
        server, env = self.gemini(flaky)
        self.assertEqual(self.cli("review", "--date", "2026-11-14", env=env)[0], 0)
        self.assertEqual(len(server.requests), 2)
        self.assertIn("could not be used", server.requests[1]["body"]["contents"][0]["parts"][0]["text"])

        bad = answer(a=scores(readability=9))
        server, env = self.gemini(lambda s, p, sc, n: (200, MockGemini.wrap(bad)))
        (self.folder / "review-gemini.md").unlink()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            self.cli("review", "--date", "2026-11-14", env=env)
        self.assertIn("after 2 requests", str(e.exception.code))
        self.assertIn("readability", str(e.exception.code))
        self.assertEqual(len(server.requests), 2)
        self.assertFalse((self.folder / "review-gemini.md").exists())

    def test_a_review_must_name_both_writers(self):
        with self.assertRaisesRegex(ValueError, "never names Writer B"):
            gemini_review.to_markdown(answer(md="# Review\n\nWriter A only."))

    def test_without_a_key_nothing_is_sent(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            self.cli("review", "--date", "2026-11-14", env={"GEMINI_API_KEY": ""})
        self.assertIn("GEMINI_API_KEY is not set", str(e.exception.code))


class ScoreAndUnblindTest(CompareCase):
    def write_reviews(self):
        (self.folder / "review-claude.md").write_text(review_text(scores(1, readability=4, gut=8), scores(3, 1, gut=6)))
        (self.folder / "review-gemini.md").write_text(review_text(scores(2, readability=4, gut=7), scores(2, 2, gut=6),
                                                                 body="Writer B guessed.\nWriter A was fine."))

    def test_score_combines_both_reviewers_and_stays_blind(self):
        self.write_reviews()
        code, out = self.cli("score", "--date", "2026-11-14")
        self.assertEqual(code, 0)
        self.assertIn("| Writer A | 110 | 104 | 107 | 7.5 |", out)
        self.assertIn("| Writer B | 86 | 84 | 85 | 6 |", out)
        code, named = self.cli("score", "--date", "2026-11-14", "--key", str(self.paper / ".compare" / "2026-11-14.key.json"))
        self.assertIn(f"| {self.key['A']['writer']} | 110 | 104 |", named)

    def test_unblind_writes_one_file_with_both_reviews_and_the_points(self):
        self.write_reviews()
        code, out = self.cli("unblind", "--date", "2026-11-14")
        self.assertEqual(code, 0)
        self.assertIn("reviews/2026-11-14-claude-vs-gemini.md", out)
        text = (self.paper / "reviews" / "2026-11-14-claude-vs-gemini.md").read_text()
        a, b = self.key["A"]["writer"], self.key["B"]["writer"]
        self.assertIn(f"Reviewed blind by 2 reviewers (claude, gemini), then unblinded: Writer A was {a} and Writer B was {b}.", text)
        self.assertIn("## Points", text)
        self.assertIn(f"| {a} | 110 | 104 | 107 | 7.5 |", text)
        self.assertIn("## Review by the claude reviewer\n\n### Verdict", text)
        self.assertIn(f"## Review by the gemini reviewer\n\n### Verdict\n{b} (Writer B) guessed.", text)
        self.assertIn("## Scores", text.replace("### Scores", "## Scores"))

    def test_the_name_is_unique_per_packet(self):
        (self.folder / "review-claude.md").write_text(review_text(scores(), scores()))
        self.cli("unblind", "--date", "2026-11-14")
        self.cli("unblind", "--date", "2026-11-14")  # the same packet again replaces its own file
        self.assertEqual(sorted(p.name for p in (self.paper / "reviews").iterdir()), ["2026-11-14-claude-vs-gemini.md"])
        self.cli("packet", "--date", "2026-11-14", "--seed", "1", "--b", "gemini")
        key = json.loads((self.paper / ".compare" / "2026-11-14.key.json").read_text())
        key["created_at"] = "2026-11-14T23:59:59+00:00"  # a newer packet of the same writers
        (self.paper / ".compare" / "2026-11-14.key.json").write_text(json.dumps(key))
        self.cli("unblind", "--date", "2026-11-14")
        self.cli("unblind", "--date", "2026-11-14", "--label", "V2 prompts")
        self.assertEqual(sorted(p.name for p in (self.paper / "reviews").iterdir()),
                         ["2026-11-14-claude-vs-gemini-2.md", "2026-11-14-claude-vs-gemini.md", "2026-11-14-v2-prompts.md"])

    def test_an_old_review_without_scores_is_unblinded_without_points(self):
        (self.folder / "review.md").write_text("# Review\n\n## Verdict\nWriter A and Writer B tie.\n")
        self.cli("unblind", "--date", "2026-11-14")
        text = (self.paper / "reviews" / "2026-11-14-claude-vs-gemini.md").read_text()
        self.assertNotIn("## Points", text)
        self.assertIn("No scores block in the review by claude", text)

    def test_default_labels(self):
        key = {"A": {"root": "gemini/single"}, "B": {"root": ""}}
        self.assertEqual(blind.default_label(key), "claude-vs-gemini-single")

    def test_demote_skips_fenced_blocks(self):
        self.assertEqual(blind.demote("## A\n```\n# not a heading\n```\n### B"), "### A\n```\n# not a heading\n```\n#### B")


if __name__ == "__main__":
    unittest.main()
