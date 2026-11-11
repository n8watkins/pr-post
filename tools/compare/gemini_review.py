"""A second, independent reviewer: send the blind packet and REVIEWER.md to Gemini, once.

The request carries only tools/compare/REVIEWER.md (as the system instruction)
and the packet (as the message), and asks for JSON: the review in Markdown and
the scores block. A second request is sent only when the first answer cannot
be used (not JSON, or a scores block that does not parse), so a review costs
one request, two at most. The answer is written as review-gemini.md in the
same shape as the Claude reviewer's file, so `score` and `unblind` read both.
"""

import json
from pathlib import Path

from . import score

HERE = Path(__file__).resolve().parent
REVIEWER = HERE / "REVIEWER.md"
DEFAULT_MODEL = "gemini-3.8-flash"
MAX_REQUESTS = 2

_LIST = {"type": "ARRAY", "items": {"type": "STRING"}}
WRITER_SCHEMA = {
    "type": "OBJECT",
    "properties": {**{m: _LIST for m in score.COUNTED}, "readability": {"type": "INTEGER"}, "gut_score": {"type": "NUMBER"}},
    "required": [*score.COUNTED, "readability", "gut_score"],
}
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "review_md": {"type": "STRING"},
        "scores": {"type": "OBJECT", "properties": {"A": WRITER_SCHEMA, "B": WRITER_SCHEMA}, "required": ["A", "B"]},
    },
    "required": ["review_md", "scores"],
}

LEAD = ("You are the reviewer described in the system instruction, answering through the API "
        "(see its section \"Answering through the API\"). The packet follows; it is the only file you get.\n\n")
RETRY = ("\n\nYour previous answer could not be used: {problem}\n"
         "Answer again with one JSON object matching the schema: review_md and scores with A and B.")


def system_prompt():
    return REVIEWER.read_text()


def scores_section(scores):
    block = {f"Writer {lb}": scores[lb] for lb in score.LABELS}
    return "## Scores\n\n```json\n" + json.dumps(block, indent=1, ensure_ascii=False) + "\n```\n"


def strip_scores(md):
    """The review without any Scores section the model added itself (ours is appended from the JSON)."""
    lines, out, skipping = md.splitlines(), [], False
    for line in lines:
        if line.strip().lower() == "## scores":
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out).rstrip()


def to_markdown(answer):
    """The review file's text from a parsed answer. Raises ValueError when the answer is unusable."""
    if not isinstance(answer, dict) or not isinstance(answer.get("review_md"), str) or not answer["review_md"].strip():
        raise ValueError("the answer has no review_md")
    scores = answer.get("scores")
    if not isinstance(scores, dict) or set(scores) < set(score.LABELS):
        raise ValueError("the answer's scores need both A and B")
    text = strip_scores(answer["review_md"]).replace("—", "-") + "\n\n" + scores_section(scores)
    score.parse_review(text)  # the same check score and unblind apply
    for label in score.LABELS:
        if f"Writer {label}" not in answer["review_md"]:
            raise ValueError(f"the review never names Writer {label}")
    return text


def review(client, packet_text):
    """Ask once (twice if the first answer is unusable). Returns (markdown, stats)."""
    prompt = LEAD + packet_text
    stats = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "latency_s": 0.0}
    problem = None
    for _ in range(MAX_REQUESTS):
        text, usage, info = client.generate(system_prompt(), prompt + (RETRY.format(problem=problem) if problem else ""), SCHEMA)
        stats["requests"] += 1
        for k in ("input_tokens", "output_tokens", "thinking_tokens"):
            stats[k] += usage.get(k, 0)
        stats["latency_s"] = round(stats["latency_s"] + info.get("latency_s", 0), 3)
        try:
            return to_markdown(json.loads(text)), stats
        except ValueError as e:
            problem = str(e)[:300]
    raise ValueError(f"the Gemini reviewer's answer could not be used after {MAX_REQUESTS} requests: {problem}")
