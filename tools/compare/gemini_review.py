"""A second, independent reviewer: send the blind packet and REVIEWER.md to Gemini, once.

The request carries only tools/compare/REVIEWER.md (as the system instruction)
and the packet (as the message), and asks for JSON: the review in Markdown and
the scores block. A second request is sent only when the first answer cannot
be used (not JSON, or a scores block that does not parse), so a review costs
one request, two at most. The answer is written as review-gemini.md in the
same shape as the Claude reviewer's file, so `score` and `unblind` read both.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import score

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from writers.gemini import api  # noqa: E402

REVIEWER = HERE / "REVIEWER.md"
DEFAULT_MODEL = "gemini-3.5-flash"
MAX_REQUESTS = 2
# Statuses where the server did no work, so asking again costs nothing: rate limit and overload.
NOT_PROCESSED = {429, 503}
MAX_WAITS = 2


class StreamClient:
    """generateContent over server-sent events, for one long answer.

    A review answer is long, and without streaming the whole wait counts against
    one read timeout. Streaming applies the timeout to each gap between chunks.
    A timeout or a dropped connection is never retried, because the request may
    have been processed and billed; a 429 or 503 (nothing processed) waits and
    tries again, at most MAX_WAITS times.
    """

    def __init__(self, model=DEFAULT_MODEL, api_key=None, base=None, timeout=300, sleep=time.sleep):
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self.base = (base or os.environ.get("GEMINI_API_BASE") or api.DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self.sleep = sleep
        if not self.api_key:
            raise api.GeminiError("GEMINI_API_KEY is not set (run through pass-env).")

    def generate(self, system, prompt, schema):
        body = json.dumps(api.request_body(system, prompt, schema)).encode()
        url = f"{self.base}/models/{self.model}:streamGenerateContent?alt=sse"
        started = time.monotonic()
        for attempt in range(MAX_WAITS + 1):
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text, meta, finish = self._read(r)
                break
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                if e.code in NOT_PROCESSED and attempt < MAX_WAITS:
                    self.sleep(api._retry_delay(raw, attempt + 2))
                    continue
                try:
                    msg = json.loads(raw).get("error", {}).get("message", "")
                except ValueError:
                    msg = ""
                raise api.GeminiError(f"Gemini API answered {e.code}: {msg[:200]}".rstrip(": ")) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                raise api.GeminiError(f"Gemini API stopped answering ({getattr(e, 'reason', e)}); not retried, since it may have been billed") from None
        usage = {"input_tokens": int(meta.get("promptTokenCount") or 0),
                 "output_tokens": int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0),
                 "thinking_tokens": int(meta.get("thoughtsTokenCount") or 0)}
        return text, usage, {"latency_s": round(time.monotonic() - started, 3), "transport_retries": attempt, "finish_reason": finish}

    @staticmethod
    def _read(response):
        parts, meta, finish = [], {}, ""
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = json.loads(line[5:])
            meta = chunk.get("usageMetadata") or meta
            for cand in chunk.get("candidates") or []:
                finish = cand.get("finishReason") or finish
                parts += [p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [] if not p.get("thought")]
        if not parts:
            raise api.GeminiError(f"Gemini returned no answer ({finish or 'empty stream'})")
        return "".join(parts), meta, finish

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
    text = strip_scores(answer["review_md"]).replace("\u2014", "-") + "\n\n" + scores_section(scores)
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
