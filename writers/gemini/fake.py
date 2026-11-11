"""A stand-in for Gemini, for --mock and the tests: answers from the request text alone, never from the network.

It writes plausible, check-passing answers by linking to the items in the
trimmed brief, so the whole pipeline (checks, assembly, saving) runs without a key.
"""

import json
import re
import time

ITEM = re.compile(r"^- \[(i\d+)\] (\S+) (.*?)(?: <(https?://\S+)>| \| link: \[[^\]]*\]\((https?://\S+?)\))$", re.M)
PERSON = re.compile(r"^### (.+) \(id: ([a-z0-9-]+)\)$", re.M)


TOP = re.compile(r"^#### Top news.*?\n\n(.*?)\n\n", re.M | re.S)


def _items(text):
    return [{"id": m.group(1), "title": m.group(3), "url": m.group(4) or m.group(5)} for m in ITEM.finditer(text)]


def _person_answer(name, block, schema_props, required, weekly):
    top = TOP.search(block)
    items = _items(top.group(1)) if top else []
    items = items or _items(block)
    first = items[0] if items else None
    second = items[1] if len(items) > 1 else first
    link = lambda i: f"[{' '.join(i['title'].split()[:3])}]({i['url']})" if i else "their repos"
    you = "Voice: Second person" in block or "This person is the reader" in block
    who = "You" if you else name
    if weekly:
        body = f"## TL;DR\n- {who} shipped {link(first)}.\n\n## Work\n- {who} kept the project moving, see {link(first)}.\n"
    else:
        body = f"- {who} shipped a change to the project: {link(first)}.\n- {who} also kept working on it: {link(second)}.\n"
    answer = {
        "report_md": body,
        "story": {"beat": "Tools desk", "headline": f"{name} keeps shipping", "summary": f"{name} had a steady stretch."},
        "profile_lines": [],
        "quote_candidate_ids": [i["id"] for i in items[:2]],
    }
    if "profile_md" in required or ("profile_md" in schema_props and "They have no profile yet" in block):
        answer["profile_md"] = f"# {name}\n\nA builder.\n"
    return answer


def _front():
    return {"tldr": ["Busy days across the beat.", "The lead story is inside.", "More notes further down."],
            "weather": "Sunny with a chance of merges.",
            "lead": {"headline": "The beat keeps shipping", "deck": "A steady stretch for the builders.", "body": ["Work landed all over."]},
            "corrections": ["No corrections this time."]}


def respond(system, prompt, schema):
    """The answer (a dict) Gemini might give to this request."""
    props = schema.get("properties", {})
    weekly = "## The weekly report" in prompt
    if "people" in props:
        blocks = PERSON.split(prompt)
        people = []
        for i in range(1, len(blocks), 3):
            name, pid, block = blocks[i], blocks[i + 1], blocks[i + 2]
            a = _person_answer(name, block, props["people"]["items"]["properties"], [], weekly)
            people.append({"id": pid, **a})
        return {"people": people, **_front()}
    if "report_md" in props:
        m = PERSON.search(prompt)
        return _person_answer(m.group(1) if m else "They", prompt, props, schema.get("required", []), weekly)
    return _front()


def usage_for(system, prompt, answer_text):
    return {"input_tokens": (len(system) + len(prompt)) // 4, "output_tokens": len(answer_text) // 4, "thinking_tokens": 0}


class FakeClient:
    """Has the same generate() as api.Client."""

    model = "mock"

    def generate(self, system, prompt, schema):
        started = time.monotonic()
        text = json.dumps(respond(system, prompt, schema))
        return text, usage_for(system, prompt, text), {"latency_s": round(time.monotonic() - started, 3), "transport_retries": 0}
