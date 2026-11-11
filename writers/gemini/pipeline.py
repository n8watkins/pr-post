"""Stages 1 to 4 of the Gemini writer: build the requests, call the model, check, assemble.

Split mode (the default) sends one request per active person (stage 1) and one
edition request that sees only their answers (stage 2). Single mode (variant B)
sends everything in one request. Either way stage 3 assembles in code and
stage 4's checks run on every answer, with one retry that names the problems.
"""

import datetime as dt
import hashlib
import json
import re
import time
from pathlib import Path
from string import Template

from . import api, checks
from .prepare import QUOTABLE

PROMPTS = Path(__file__).resolve().parent / "prompts"
PROMPT_VERSION = "v1"
PREVIOUS_ANSWER_LIMIT = 8000

STORY = {"type": "OBJECT", "properties": {"beat": {"type": "STRING"}, "headline": {"type": "STRING"}, "summary": {"type": "STRING"}},
         "required": ["beat", "headline", "summary"]}
PERSON_FIELDS = {
    "report_md": {"type": "STRING"},
    "story": STORY,
    "profile_lines": {"type": "ARRAY", "items": {"type": "STRING"}},
    "lead_score": {"type": "INTEGER"},
    "quote_candidate_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
}
PERSON_REQUIRED = ["report_md", "story", "profile_lines", "lead_score", "quote_candidate_ids"]
EDITION_FIELDS = {
    "tldr": {"type": "ARRAY", "items": {"type": "STRING"}, "minItems": 3, "maxItems": 5},
    "weather": {"type": "STRING"},
    "lead": {"type": "OBJECT", "properties": {"headline": {"type": "STRING"}, "deck": {"type": "STRING"},
                                              "body": {"type": "ARRAY", "items": {"type": "STRING"}, "minItems": 1, "maxItems": 2}},
             "required": ["headline", "deck", "body"]},
    "corrections": {"type": "ARRAY", "items": {"type": "STRING"}},
}
EDITION_REQUIRED = ["tldr", "weather", "lead", "corrections"]


def person_schema(needs_profile):
    props = dict(PERSON_FIELDS)
    required = list(PERSON_REQUIRED)
    if needs_profile:
        props["profile_md"] = {"type": "STRING"}
        required.append("profile_md")
    return {"type": "OBJECT", "properties": props, "required": required}


def edition_schema():
    return {"type": "OBJECT", "properties": dict(EDITION_FIELDS), "required": list(EDITION_REQUIRED)}


def single_schema():
    person = {"type": "OBJECT", "properties": {"id": {"type": "STRING"}, **PERSON_FIELDS, "profile_md": {"type": "STRING"}},
              "required": ["id"] + PERSON_REQUIRED}
    return {"type": "OBJECT",
            "properties": {"people": {"type": "ARRAY", "items": person}, "lead_id": {"type": "STRING"}, **EDITION_FIELDS},
            "required": ["people", "lead_id"] + EDITION_REQUIRED}


# ---------- prompts ----------

def load_prompts(version=PROMPT_VERSION):
    folder = PROMPTS / version
    files = {f.stem: f.read_text() for f in sorted(folder.glob("*.md"))}
    if not files:
        raise FileNotFoundError(f"No prompts in {folder}")
    digest = hashlib.sha256("".join(f"{k}\n{v}" for k, v in sorted(files.items())).encode()).hexdigest()[:12]
    return {"version": version, "sha": digest, "files": files}


def fill(prompts, template, /, **values):
    return Template(prompts["files"][template]).substitute(**values)


def long_day(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def short_day(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d:%b} {d.day}"


def title_for(person, kind):
    span = f"{short_day(person['start'])} to {short_day(person['end'])}"
    return f"# {person['name']}: {span}" if kind == "weekly" else f"# {person['name']} update: {span}"


def kind_name(kind):
    return "weekly issue" if kind == "weekly" else "midweek update"


def person_block(prompts, person, kind):
    notes = []
    if person["self"]:
        notes.append("- This person is the reader: write their report in the second person"
                     + (" and add the \"How you look from the outside\" section." if kind == "weekly" else "."))
    if not person.get("profile"):
        notes.append("- They have no profile yet: also return `profile_md`.")
    return fill(prompts, "person", name=person["name"], id=person["id"], start_long=long_day(person["start"]),
                end_long=long_day(person["end"]), title=title_for(person, kind)[2:], known_for=person["known_for"] or "(not given)",
                notes="\n".join(notes) + ("\n" if notes else ""), profile=person.get("profile") or "(none yet)", brief=person["brief"])


def system_prompt(prompts, prep):
    return fill(prompts, "system", reader=prep["reader"])


def person_request(prompts, prep, person):
    kind = prep["kind"]
    prompt = fill(prompts, "one_person", kind_name="weekly" if kind == "weekly" else "midweek update",
                  report_rules=prompts["files"][f"format_{kind}"], fields=prompts["files"]["fields"], person=person_block(prompts, person, kind))
    return {"stage": "person", "id": person["id"], "system": system_prompt(prompts, prep), "prompt": prompt,
            "schema": person_schema(not person.get("profile"))}


def guesses_text(previous):
    """Sentences of the previous edition that guessed ("probably", "looks like"), for its corrections."""
    if not previous:
        return "None: this is the first edition."
    texts = list(previous.get("tldr", [])) + [previous.get("lead", {}).get("deck", "")] + list(previous.get("lead", {}).get("body", []))
    texts += [s.get("summary", "") for s in previous.get("stories", [])]
    found = [s.strip() for t in texts for s in re.split(r"(?<=[.!?])\s+", t or "") if re.search(r"\b(probably|looks like)\b", s, re.I)]
    return "\n".join(f"- {s}" for s in found) if found else "None."


def quiet_text(prep):
    quiet = [p for p in prep["people"] if p["status"] == "quiet"]
    return "\n".join(f"- {p['name']}" for p in quiet) if quiet else "Nobody."


def edition_request(prompts, prep, answers, lead_id, previous):
    """Stage 2: sees only stage 1's answers (no briefs)."""
    by_id = {p["id"]: p for p in prep["people"]}
    lead = by_id[lead_id]
    stories = "\n".join(
        f"- {by_id[pid]['name']} ({a['story']['beat']}): {a['story']['headline']}. {a['story']['summary']}"
        for pid, a in answers.items())
    prompt = fill(prompts, "edition", kind_name=kind_name(prep["kind"]), date_long=long_day(prep["date"]), lead_name=lead["name"],
                  edition_cap=checks.CAPS["edition"], guesses=guesses_text(previous), lead_report=answers[lead_id]["report_full"],
                  stories=stories or "None.", quiet=quiet_text(prep))
    return {"stage": "edition", "id": None, "system": system_prompt(prompts, prep), "prompt": prompt, "schema": edition_schema()}


def single_request(prompts, prep, previous):
    kind = prep["kind"]
    active = [p for p in prep["people"] if p["status"] == "active"]
    prompt = fill(prompts, "single", kind_name=kind_name(kind), date_long=long_day(prep["date"]),
                  report_rules=prompts["files"][f"format_{kind}"], fields=prompts["files"]["fields"], edition_cap=checks.CAPS["edition"],
                  guesses=guesses_text(previous), quiet=quiet_text(prep),
                  people="\n".join(person_block(prompts, p, kind) for p in active))
    return {"stage": "single", "id": None, "system": system_prompt(prompts, prep), "prompt": prompt, "schema": single_schema()}


# ---------- checks per answer ----------

def check_person(answer, person, kind, input_text):
    problems = []
    full = title_for(person, kind) + "\n\n" + answer.get("report_md", "")
    problems += checks.check_text("report_md", full, input_text, checks.CAPS[kind])
    story = answer.get("story") or {}
    if checks.words(story.get("summary", "")) > checks.STORY_WORDS:
        problems.append(f"story.summary has {checks.words(story.get('summary', ''))} words; the limit is {checks.STORY_WORDS}")
    problems += checks.check_text("story", f"{story.get('headline', '')}\n{story.get('summary', '')}", input_text)
    problems += checks.check_text("profile_lines", "\n".join(answer.get("profile_lines") or []), input_text)
    if answer.get("profile_md"):
        problems += checks.check_text("profile_md", answer["profile_md"], input_text, 400)
    return problems


def edition_text(answer):
    lead = answer.get("lead") or {}
    return "\n".join([*answer.get("tldr", []), answer.get("weather", ""), lead.get("headline", ""), lead.get("deck", ""),
                      *lead.get("body", []), *answer.get("corrections", [])])


def check_edition(answer, input_text):
    problems = []
    for i, b in enumerate(answer.get("tldr") or []):
        if checks.words(b) > checks.TLDR_WORDS:
            problems.append(f"tldr[{i}] has {checks.words(b)} words; the limit is {checks.TLDR_WORDS}")
    problems += checks.check_text("the front page (tldr, weather, lead, corrections)", edition_text(answer), input_text, checks.CAPS["edition"])
    return problems


def check_single(answer, prep, input_text):
    problems = []
    active = {p["id"]: p for p in prep["people"] if p["status"] == "active"}
    got = {a.get("id") for a in answer.get("people") or [] if isinstance(a, dict)}
    missing = [pid for pid in active if pid not in got]
    if missing:
        problems.append(f"people is missing these ids: {', '.join(missing)}")
    for a in answer.get("people") or []:
        if isinstance(a, dict) and a.get("id") in active:
            person = active[a["id"]]
            if not person.get("profile") and not a.get("profile_md"):
                problems.append(f"people[{a['id']}] is missing profile_md")
            problems += [f"people[{a['id']}].{p}" for p in check_person(a, person, prep["kind"], input_text)]
    if answer.get("lead_id") not in active:
        problems.append("lead_id must be the id of one of the people")
    return problems + check_edition(answer, input_text)


# ---------- calling, with one retry ----------

class Runner:
    """Sends requests through a client, checks each answer, retries once, and logs usage."""

    def __init__(self, client, prompts, min_interval=0.0, sleep=time.sleep):
        self.client = client
        self.prompts = prompts
        self.min_interval = min_interval
        self.sleep = sleep
        self.calls = []
        self._last = None

    def _send(self, req, prompt):
        if self.min_interval and self._last is not None:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
        try:
            return self.client.generate(req["system"], prompt, req["schema"])
        finally:
            self._last = time.monotonic()

    def run(self, req, check):
        """Returns (answer or None, problems still failing)."""
        log = {"stage": req["stage"], "id": req["id"], "attempts": 0, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
               "latency_s": 0.0, "transport_retries": 0, "problems_first": [], "problems_final": [], "error": ""}
        self.calls.append(log)
        prompt = req["prompt"]
        answer, problems = None, []
        for attempt in range(2):
            log["attempts"] += 1
            try:
                text, usage, info = self._send(req, prompt)
            except api.GeminiError as e:
                log["error"] = str(e)
                return None, [str(e)]
            for k in ("input_tokens", "output_tokens", "thinking_tokens"):
                log[k] += usage[k]
            log["latency_s"] = round(log["latency_s"] + info["latency_s"], 3)
            log["transport_retries"] += info["transport_retries"]
            try:
                answer = checks.no_em_dash(json.loads(text))
                problems = checks.check_shape(answer, req["schema"])
                if not problems:
                    problems = check(answer)
            except ValueError:
                answer, problems = None, ["the answer was not valid JSON"]
            if attempt == 0:
                log["problems_first"] = problems
            if not problems:
                break
            if attempt == 0:
                prompt = req["prompt"] + fill(self.prompts, "retry", problems="\n".join(f"- {p}" for p in problems),
                                              previous=text[:PREVIOUS_ANSWER_LIMIT])
        log["problems_final"] = problems
        if answer is not None and checks.check_shape(answer, req["schema"]):
            answer = None  # wrong shape after the retry: nothing usable
        return answer, problems


# ---------- stage 3: assemble in code ----------

def report_full(person, kind, body):
    return title_for(person, kind) + "\n\n" + body.strip() + "\n"


def quiet_report(person, kind):
    return title_for(person, kind) + "\n\n" + f"Quiet since {long_day(person['start'])}.\n"


def quiet_story(person):
    return {"id": person["id"], "beat": "Quiet desk", "headline": f"{person['name']} keeps it quiet",
            "summary": f"Nothing new in public since {short_day(person['start'])}."}


def pick_quote(prep, answers, order):
    """The quote of the week, copied by code from the data: never model-written."""
    by_id = {p["id"]: p for p in prep["people"]}
    for pid in order:
        person = by_id[pid]
        items = {i["id"]: i for i in person["items"]}
        for cid in answers[pid].get("quote_candidate_ids") or []:
            item = items.get(cid)
            if not item or item["kind"] not in QUOTABLE or not item.get("url"):
                continue
            text = person["titles"].get(item["url"])
            if not text:
                text = item["title"]
                if text.endswith("..."):
                    continue  # the brief shortened it, so it is not an exact copy
            return {"text": text, "url": item["url"], "by": person["name"],
                    "where": item["repo"].split("/")[-1] if item["repo"] else "YouTube",
                    "date": short_day(item["date"]) if item["date"] else ""}
    return None


def lead_sources(body):
    links = re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", body or "")
    out, seen = [], set()
    for label, url in links:
        if url not in seen:
            seen.add(url)
            out.append({"label": label if len(label) <= 40 else label[:37].rsplit(" ", 1)[0] + "...", "url": url})
    return out[:3]


def births_and_obituaries(prep):
    births, obits = [], []
    for p in prep["people"]:
        if p["status"] != "active":
            continue
        for i in p["items"]:
            if i["kind"] == "birth":
                births.append({"repo": i["repo"], "text": i["title"] or "New repo."})
        for g in p["archived"]:
            obits.append({"name": g["repo"].split("/")[-1], "url": g["url"], "text": "Archived."})
    return births, obits


def choose_lead(prep, answers):
    by_id = {p["id"]: p for p in prep["people"]}
    return max(answers, key=lambda pid: (max(0, min(10, int(answers[pid].get("lead_score") or 0))), by_id[pid]["starred"],
                                         len(by_id[pid]["items"])))


def assemble(prep, answers, front, lead_id, model, prompts, mode):
    """Files to write, as {relative path: text}, plus the edition dict."""
    kind, date = prep["kind"], prep["date"]
    files = {}
    for p in prep["people"]:
        if p["status"] == "quiet":
            files[f"reports/{p['id']}/{date}-{kind}.md"] = quiet_report(p, kind)
        elif p["status"] == "active" and p["id"] in answers:
            a = answers[p["id"]]
            files[f"reports/{p['id']}/{date}-{kind}.md"] = a["report_full"]
            if a.get("profile_md") and not p.get("profile"):
                files[f"profiles/{p['id']}.md"] = a["profile_md"].strip() + "\n"
            elif a.get("profile_lines") and p.get("profile"):
                lines = "".join(f"- {date}: {line.strip()}\n" for line in a["profile_lines"] if line.strip())
                files[f"profiles/{p['id']}.md"] = p["profile"].rstrip("\n") + "\n" + lines
    if front is None or lead_id is None:
        return files, None
    order = [lead_id] + sorted((pid for pid in answers if pid != lead_id), key=lambda pid: -int(answers[pid].get("lead_score") or 0))
    stories = [{"id": pid, **answers[pid]["story"]} for pid in order[1:]]
    if kind == "weekly":
        stories += [quiet_story(p) for p in prep["people"] if p["status"] == "quiet"]
    else:
        stories = stories[:3]
    births, obits = births_and_obituaries(prep)
    lead = front["lead"]
    edition = {
        "date": date, "kind": kind, "source": model, "prompt_version": prompts["version"], "prompt_sha": prompts["sha"], "mode": mode,
        "tldr": front["tldr"], "weather": front["weather"],
        "lead": {"id": lead_id, "headline": lead["headline"], "deck": lead["deck"], "body": lead["body"],
                 "sources": lead_sources(answers[lead_id]["report_full"])},
        "stories": stories, "obituaries": obits, "births": births,
        "quote": pick_quote(prep, answers, order), "corrections": front["corrections"],
    }
    edition["checks"] = {"edition_words": checks.words(edition_text(front) + "\n" + "\n".join(f"{s['headline']} {s['summary']}" for s in stories))}
    if edition["checks"]["edition_words"] > checks.CAPS["edition"]:
        edition["checks"]["warning"] = (f"with every story the edition has {edition['checks']['edition_words']} words, "
                                        f"over the {checks.CAPS['edition']}-word cap the front page alone keeps")
    files[f"editions/{date}-{kind}.json"] = json.dumps(edition, indent=1, ensure_ascii=False) + "\n"
    return files, edition


# ---------- the two modes ----------

def run_split(runner, prep, previous):
    prompts = runner.prompts
    answers, failed = {}, []
    for p in prep["people"]:
        if p["status"] != "active":
            continue
        req = person_request(prompts, prep, p)
        answer, problems = runner.run(req, lambda a, p=p, req=req: check_person(a, p, prep["kind"], req["prompt"]))
        if answer is None:
            failed.append(p["id"])
            continue
        answer["report_full"] = report_full(p, prep["kind"], answer["report_md"])
        answers[p["id"]] = answer
    if not answers:
        return answers, None, None, failed
    lead_id = choose_lead(prep, answers)
    req = edition_request(prompts, prep, answers, lead_id, previous)
    front, _ = runner.run(req, lambda a: check_edition(a, req["prompt"]))
    if front is None:
        failed.append("edition")
    return answers, front, lead_id, failed


def run_single(runner, prep, previous):
    req = single_request(runner.prompts, prep, previous)
    if not any(p["status"] == "active" for p in prep["people"]):
        return {}, None, None, []
    answer, _ = runner.run(req, lambda a: check_single(a, prep, req["prompt"]))
    if answer is None:
        return {}, None, None, ["single"]
    by_id = {p["id"]: p for p in prep["people"] if p["status"] == "active"}
    answers = {}
    for a in answer["people"]:
        if a.get("id") in by_id and a["id"] not in answers:
            a = dict(a)
            a["report_full"] = report_full(by_id[a["id"]], prep["kind"], a["report_md"])
            answers[a["id"]] = a
    failed = [pid for pid in by_id if pid not in answers]
    lead_id = answer["lead_id"] if answer["lead_id"] in answers else (choose_lead(prep, answers) if answers else None)
    front = {k: answer[k] for k in EDITION_REQUIRED}
    return answers, front, lead_id, failed


# ---------- planning (--dry-run) ----------

PLACEHOLDER_STORY = {"beat": "Tools desk", "headline": "A placeholder headline of a few words",
                     "summary": "A placeholder summary about as long as a real one, thirty words or a little under, standing in for the answer stage one returns."}
OUTPUT_ESTIMATE = {"person": {"weekly": 520, "update": 280}, "edition": 450}


def plan(prompts, prep, mode, previous):
    """Every request the run would send, with stage 2 built from placeholder answers. No model is called."""
    reqs = []
    active = [p for p in prep["people"] if p["status"] == "active"]
    if mode == "single":
        if active:
            reqs.append(single_request(prompts, prep, previous))
    else:
        fake = {}
        for p in active:
            reqs.append(person_request(prompts, prep, p))
            body = ("## TL;DR\n" + "- placeholder words " * 60) if prep["kind"] == "weekly" else "- placeholder words " * 30
            fake[p["id"]] = {"story": PLACEHOLDER_STORY, "report_full": report_full(p, prep["kind"], body), "lead_score": 0}
        if fake:
            reqs.append(edition_request(prompts, prep, fake, next(iter(fake)), previous))
    out = []
    for r in reqs:
        est_in = api.estimate_tokens(r["system"] + r["prompt"] + json.dumps(r["schema"]))
        if r["stage"] == "person":
            est_out = OUTPUT_ESTIMATE["person"][prep["kind"]]
        elif r["stage"] == "edition":
            est_out = OUTPUT_ESTIMATE["edition"]
        else:
            est_out = OUTPUT_ESTIMATE["person"][prep["kind"]] * len(active) + OUTPUT_ESTIMATE["edition"]
        out.append({"stage": r["stage"], "id": r["id"], "est_input_tokens": est_in, "est_output_tokens": est_out})
    return out
