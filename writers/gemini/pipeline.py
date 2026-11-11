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
PROMPT_VERSION = "v2"
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
    """The prompt files of one version, plus its optional settings.json (such as {"temperature": 0.3}).

    The settings are part of the version and of its sha, so every output records
    the sampling it was written with.
    """
    folder = PROMPTS / version
    files = {f.stem: f.read_text() for f in sorted(folder.glob("*.md"))}
    if not files:
        raise FileNotFoundError(f"No prompts in {folder}")
    settings_file = folder / "settings.json"
    settings_text = settings_file.read_text() if settings_file.is_file() else ""
    settings = json.loads(settings_text) if settings_text else {}
    digest = hashlib.sha256(("".join(f"{k}\n{v}" for k, v in sorted(files.items())) + settings_text).encode()).hexdigest()[:12]
    return {"version": version, "sha": digest, "files": files, "settings": settings}


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


def first_name(person):
    return person["name"].split()[0] if person["name"] else person["id"]


def perspective(person):
    """The one line that says who "you" is, so the model never addresses anyone else as the reader."""
    if person["self"]:
        return ("Second person: this person IS the reader. "
                "Write \"you shipped...\", \"your repo...\"; never their name or \"they\".")
    return (f"Third person: {person['name']} is NOT the reader. Call them \"{first_name(person)}\" or \"they\"; "
            "never write \"you\" or \"your\" in this report.")


def top_news_text(person):
    """The window's most newsworthy items, best first, computed in prepare.py, as lines for the prompt."""
    lines = []
    for h in person.get("highlights") or []:
        ref = f"[{h['id']}] " if h["id"] else ""
        lines.append(f"- {ref}{h['label']}: {h['repo']} {h['title']} <{h['url']}>".replace("  ", " "))
    return "\n".join(lines) if lines else "(nothing stands out)"


def person_block(prompts, person, kind):
    notes = []
    if person["self"]:
        notes.append("- This person is the reader: write their report in the second person"
                     + (" and add the \"How you look from the outside\" section." if kind == "weekly" else "."))
    if not person.get("profile"):
        notes.append("- They have no profile yet: also return `profile_md`.")
    return fill(prompts, "person", name=person["name"], id=person["id"], start_long=long_day(person["start"]),
                end_long=long_day(person["end"]), title=title_for(person, kind)[2:], known_for=person["known_for"] or "(not given)",
                notes="\n".join(notes) + ("\n" if notes else ""), profile=person.get("profile") or "(none yet)", brief=person["brief"],
                perspective=perspective(person), top_news=top_news_text(person), first_name=first_name(person))


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


def email_line(report):
    """The first bullet of a report: the person's line in the email."""
    for line in (report or "").splitlines():
        if line.startswith("- "):
            return line[2:].strip()
    return next((line.strip() for line in (report or "").splitlines()[1:] if line.strip()), "")


def edition_request(prompts, prep, answers, lead_id, previous):
    """Stage 2: sees only stage 1's answers (no briefs)."""
    by_id = {p["id"]: p for p in prep["people"]}
    lead = by_id[lead_id]
    stories = "\n".join(
        f"- {by_id[pid]['name']} ({a['story']['beat']}): {a['story']['headline']}. {a['story']['summary']}"
        for pid, a in answers.items())
    reader_note = ' (the reader: write "you")'
    lines = "\n".join(f"- {by_id[pid]['name']}{reader_note if by_id[pid]['self'] else ''}: {email_line(a['report_full'])}"
                      for pid, a in answers.items())
    reader = next((p for p in prep["people"] if p["self"]), None)
    prompt = fill(prompts, "edition", kind_name=kind_name(prep["kind"]), date_long=long_day(prep["date"]), lead_name=lead["name"],
                  edition_cap=checks.CAPS["edition"], guesses=guesses_text(previous), lead_report=answers[lead_id]["report_full"],
                  stories=stories or "None.", quiet=quiet_text(prep), lines=lines or "None.",
                  reader_name=reader["name"] if reader else "(nobody on the list)")
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

QUIET_RE = re.compile(r"^\s*quiet since\b", re.I)
NEWS_SCORE = 8  # a highlight this newsworthy (a release, new repo, video or headline PR) must not be called quiet
OWN_WORDS = 4   # a bullet must say at least this much in its own words, not only a linked title
TOP_CHOICES = 3  # the report must cover at least one of the top items prepare.py ranked
LABEL_WORDS = 6  # a link label longer than this is a pasted title


def release_only(bullet, archived=()):
    """A bullet that only announces releases or archived repos: with no notes there is nothing more to say than the name."""
    urls = checks.link_urls(bullet)
    gone = {u.rstrip("/") for u in archived}
    return bool(urls) and all("/releases/" in u or u.rstrip("/") in gone for u in urls) and checks.words_outside_links(bullet) >= 2


def brief_urls(person):
    """Every address in the person's trimmed brief: the only places a report may link to."""
    return set(checks.URL_RE.findall(person.get("brief") or "")) | {g["url"] for g in person.get("archived") or []}


def covers(highlight, urls):
    """Whether a report linking `urls` covers this highlight (any link into a new or archived repo, any of a repo's releases)."""
    url = highlight["url"].rstrip("/")
    if highlight["kind"] in ("birth", "archived"):
        return any(u.rstrip("/") == url or u.startswith(url + "/") for u in urls)
    if highlight["kind"] == "release":
        base = url.split("/releases/")[0]
        return any(u.startswith(base + "/releases") for u in urls)
    return url in {u.rstrip("/") for u in urls}


SHA_LABEL = re.compile(r"\[(commit )?([0-9a-f]{7})[0-9a-f]{33}\]")


def normalize_answer(answer, kind):
    """Fix in code what needs no model: an update's plain lines become bullets, and a full commit hash in a link label
    is cut to its first seven characters. Changes `answer` in place and returns it."""
    if not isinstance(answer, dict) or not isinstance(answer.get("report_md"), str):
        return answer
    body = answer["report_md"]
    if kind == "update" and not QUIET_RE.match(body.strip()):
        body = "\n".join(f"- {line.strip()}" if line.strip() and not line.lstrip().startswith(("- ", "#", "* ")) else
                         re.sub(r"^\s*\*\s", "- ", line) for line in body.splitlines())
    answer["report_md"] = SHA_LABEL.sub(lambda m: f"[{m.group(1) or ''}{m.group(2)}]", body)
    return answer


def check_person(answer, person, kind, input_text):
    problems = []
    body = answer.get("report_md", "")
    full = title_for(person, kind) + "\n\n" + body
    problems += checks.check_text("report_md", full, input_text, checks.CAPS[kind])
    story = answer.get("story") or {}
    if checks.words(story.get("summary", "")) > checks.STORY_WORDS:
        problems.append(f"story.summary has {checks.words(story.get('summary', ''))} words; the limit is {checks.STORY_WORDS}")
    problems += checks.check_text("story", f"{story.get('headline', '')}\n{story.get('summary', '')}", input_text)
    problems += checks.check_text("profile_lines", "\n".join(answer.get("profile_lines") or []), input_text)
    if answer.get("profile_md"):
        problems += checks.check_text("profile_md", answer["profile_md"], input_text, 400)
    if "brief" not in person:
        return problems
    # Links only to items of this person's brief, not to anything else in the prompt (the profile, other people).
    allowed = brief_urls(person)
    outside = sorted({u.rstrip(".,;:") for u in checks.URL_RE.findall(body + "\n" + "\n".join(answer.get("profile_lines") or []))}
                     - {u.rstrip(".,;:") for u in allowed})
    outside = [u for u in outside if u in input_text]  # addresses not in the input at all are already reported above
    if outside:
        problems.append(f"report_md links to addresses that are not items of this person's brief: {', '.join(outside[:5])}; "
                        "link only to the brief's items")
    # Who "you" is.
    written = body + "\n" + story.get("headline", "") + "\n" + story.get("summary", "")
    if not person["self"] and checks.second_person(written, person.get("brief", "")):
        problems.append(f"{person['name']} is not the reader, but the report or story says \"you\" or \"your\"; "
                        f"write about {first_name(person)} in the third person")
    quiet = bool(QUIET_RE.match(body.strip()))
    if person["self"] and not quiet and not checks.second_person(body):
        problems.append("this person is the reader: write the report in the second person (\"you shipped...\")")
    # Quiet only when the brief is quiet.
    news = [h for h in person.get("highlights") or [] if h["score"] >= NEWS_SCORE]
    if quiet and news:
        problems.append(f"the report says quiet, but the brief has news: {news[0]['label']} {news[0]['repo']} {news[0]['title']} "
                        f"<{news[0]['url']}>; report it")
    if not quiet:
        bl = checks.bullets(body)
        item_bullets = [(sec, b) for sec, b in bl if not sec.startswith(checks.UNLINKED_SECTIONS)]
        unlinked = [b for _, b in item_bullets if not checks.link_urls(b)]
        if unlinked:
            problems.append(f"every bullet needs a Markdown link to its item in the brief; these have none: "
                            + "; ".join(f"\"{b[:60]}\"" for b in unlinked[:3]))
        bare = [b for _, b in item_bullets if checks.link_urls(b) and not release_only(b, [g["url"] for g in person.get("archived") or []]) and checks.words_outside_links(b) < OWN_WORDS]
        if bare:
            problems.append("these bullets are only a linked title; say in plain words what changed and why it matters, then link: "
                            + "; ".join(f"\"{b[:60]}\"" for b in bare[:3]))
        long_labels = [label for _, b in item_bullets for label, _ in checks.MD_LINK.findall(b) if checks.words(label) > LABEL_WORDS]
        if long_labels:
            problems.append("link labels must be short (\"PR 15162\", \"v1.5.2\", the repo name), not whole titles: "
                            + "; ".join(f"\"{label[:60]}\"" for label in long_labels[:3]))
        if kind == "update":
            if not 2 <= len(bl) <= 4 and sum(1 for _ in person.get("items") or []) > 1:
                problems.append(f"the update has {len(bl)} bullets; write 2 to 4")
            long = [b for _, b in bl if checks.words(b) > checks.BULLET_WORDS]
            if long:
                problems.append(f"{len(long)} bullets have more than {checks.BULLET_WORDS} words; shorten them")
        top = (person.get("highlights") or [])[:TOP_CHOICES]
        urls = checks.link_urls(body)
        covered = [h for h in top if covers(h, urls)]
        if top and top[0]["score"] >= NEWS_SCORE and not (covers(top[0], urls) or len(covered) >= min(2, len(top))):
            problems.append("the report leaves out the biggest news; cover the first of these, or two of them, first: "
                            + "; ".join(f"{h['label']} {h['repo']} {h['title'][:60]} <{h['url']}>" for h in top))
    hyped = checks.hype(written)
    if hyped:
        problems.append(f"no hype words ({', '.join(hyped)}); say plainly what changed")
    return problems


HARD = ("not in the input", "not items of this person's brief", "is not the reader")


def hard_problems(problems):
    """Problems that make an answer wrong rather than imperfect: untraceable numbers or links, or the wrong "you"."""
    return [p for p in problems if any(h in p for h in HARD)]


def edition_text(answer):
    lead = answer.get("lead") or {}
    return "\n".join([*answer.get("tldr", []), answer.get("weather", ""), lead.get("headline", ""), lead.get("deck", ""),
                      *lead.get("body", []), *answer.get("corrections", [])])


def check_edition(answer, input_text, lead_report=""):
    """Checks for the front page; `lead_report` is the lead's report, which the lead must retell, not copy."""
    problems = []
    lead = answer.get("lead") or {}
    linked = [s for s in [*answer.get("tldr", []), lead.get("deck", ""), *lead.get("body", [])] if checks.MD_LINK.search(s or "")]
    if linked:
        problems.append("the front page is plain text: no Markdown links in the TL;DR, deck or lead body (the sources are added by code)")
    copied = checks.copied_runs("\n".join([lead.get("deck", ""), *lead.get("body", [])]), lead_report) if lead_report else []
    if copied:
        problems.append("the lead copies the report word for word (\"" + "\"; \"".join(copied[:2]) + "\"); retell it in new sentences")
    for i, b in enumerate(answer.get("tldr") or []):
        if checks.words(b) > checks.TLDR_WORDS:
            problems.append(f"tldr[{i}] has {checks.words(b)} words; the limit is {checks.TLDR_WORDS}")
    problems += checks.check_text("the front page (tldr, weather, lead, corrections)", edition_text(answer), input_text, checks.CAPS["edition"])
    hyped = checks.hype(edition_text(answer))
    if hyped:
        problems.append(f"the front page uses hype words ({', '.join(hyped)}); name the concrete change instead")
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


# ---------- fallback: a plain report built from the brief by code ----------

def short_repo(repo):
    return repo.split("/")[-1] if repo else ""


def clip_text(text, limit=90):
    text = (text or "").strip()
    if text.endswith("..."):
        text = text[:-3].rstrip()
        cut = True
    else:
        cut = False
    if len(text) > limit:
        text, cut = text[:limit].rsplit(" ", 1)[0], True
    text = text.rstrip(" .,;:")
    return text + ("..." if cut else "")


def sentence(text):
    """Text ending in one stop: clipped text keeps its "...", anything else gets a period."""
    text = (text or "").strip()
    return text if not text or text.endswith(("...", ".", "!", "?")) else text + "."


def link_label(text):
    return clip_text(text, 60).replace("[", "(").replace("]", ")")


def capitalized(line):
    """A line that starts with a capital ("you shipped..." at the start of a TL;DR bullet becomes "You shipped...")."""
    line = (line or "").strip()
    return line[:1].upper() + line[1:]


def description(repo, text):
    """A repo's own description, without a leading "Name:" that repeats the repo name."""
    text = clip_text(text)
    name = short_repo(repo)
    if text.lower().startswith(name.lower() + ":"):
        text = text[len(name) + 1:].strip()
    return text


def fallback_line(h):
    """One plain, correct bullet for a highlight, using only the brief's own words."""
    name = short_repo(h["repo"])
    if h["kind"] == "birth":
        return f"New repo [{name}]({h['url']}), in its own words: \"{description(h['repo'], h['title'])}\""
    if h["kind"] == "archived":
        return f"Archived or took down [{name}]({h['url']})."
    if h["kind"] == "release":
        return f"Released [{link_label(name + ' ' + h['title'] if name.lower() not in h['title'].lower() else h['title'])}]({h['url']})."
    if h["kind"] == "video":
        return f"Posted a new video, \"{clip_text(h['title'])}\" ([video]({h['url']}))."
    verb = {"pr": "Merged", "open_pr": "Opened", "commit": "Committed"}.get(h["kind"], "Worked on")
    number = re.search(r"/pull/(\d+)", h["url"])
    label = f"PR {number.group(1)}" if number else "commit"
    return f"{verb} a change in {name}, titled \"{clip_text(h['title'])}\" ([{label}]({h['url']}))."


def fallback_answer(person, kind):
    """A minimal report and story written by code from the person's ranked brief items, for when the model's answer
    is missing or wrong. Plain, but every word and link comes from the brief."""
    tops = (person.get("highlights") or [])[:3]
    if not tops:
        return None
    lines = [f"- {fallback_line(h)}" for h in tops]
    if kind == "weekly":
        body = "## TL;DR\n" + lines[0] + "\n\n## Highlights\n" + "\n".join(lines[1:] or lines) + "\n"
    else:
        body = "\n".join(lines) + "\n"
    first = tops[0]
    story = {"beat": "From the brief", "headline": f"{person['name']}: {short_repo(first['repo'])}",
             "summary": clip_text(f"{fallback_line(first)}", 160).replace("[", "").replace("]", "")}
    story["summary"] = re.sub(r"\((https?://[^)\s]+)\)", "", story["summary"])
    quotable = [h["id"] for h in tops if h["kind"] in QUOTABLE and h["id"]]
    return {"report_md": body, "story": story, "profile_lines": [], "lead_score": min(10, max(0, first["score"] - 3)),
            "quote_candidate_ids": quotable, "fallback": True}


# ---------- stage 3: assemble in code ----------

def report_full(person, kind, body):
    return title_for(person, kind) + "\n\n" + body.strip() + "\n"


def quiet_report(person, kind):
    return title_for(person, kind) + "\n\n" + f"Quiet since {long_day(person['start'])}.\n"


def quiet_story(person):
    return {"id": person["id"], "beat": "Quiet desk", "headline": f"{person['name']} keeps it quiet",
            "summary": f"Nothing new in public since {short_day(person['start'])}."}


CONVENTIONAL = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s")


def pick_quote(prep, answers, order):
    """The quote of the week, copied by code from the data: never model-written.

    It prefers a title in someone's own voice: not the reader's own work and not a
    conventional-commit title ("perf(sandbox): ..."), falling back to those only
    when nothing else was picked.
    """
    by_id = {p["id"]: p for p in prep["people"]}
    candidates = []
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
            candidates.append(({"text": text, "url": item["url"], "by": person["name"],
                                "where": item["repo"].split("/")[-1] if item["repo"] else "YouTube",
                                "date": short_day(item["date"]) if item["date"] else ""},
                               (bool(person["self"]), bool(CONVENTIONAL.match(text)))))
    if not candidates:
        return None
    return min(enumerate(candidates), key=lambda t: (t[1][1], t[0]))[1][0]


def lead_sources(body):
    links = re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", body or "")
    out, seen = [], set()
    for label, url in links:
        if url not in seen:
            seen.add(url)
            out.append({"label": label if len(label) <= 40 else label[:37].rsplit(" ", 1)[0] + "...", "url": url})
    return out[:3]


def births_and_obituaries(prep):
    """Every new repo in a window and every archived repo, from the briefs, by code: complete and never model-written."""
    births, obits = [], []
    for p in prep["people"]:
        if p["status"] not in ("active", "quiet"):
            continue
        for i in p["items"]:
            if i["kind"] == "birth":
                when = f", {short_day(i['date'])}" if i["date"] else ""
                what = sentence(clip_text(i["title"], 120))
                births.append((i["date"], {"repo": i["repo"], "text": f"To {p['name']}{when}." + (f" {what}" if what else "")}))
        for g in p["archived"]:
            whose = "Your repo" if p["self"] else f"{p['name']}'s repo"
            obits.append({"name": g["repo"].split("/")[-1], "url": g["url"], "text": f"{whose}, archived or no longer public."})
    births.sort(key=lambda t: t[0], reverse=True)
    return [b for _, b in births], obits


def choose_lead(prep, answers):
    by_id = {p["id"]: p for p in prep["people"]}
    top = lambda pid: max((h["score"] for h in by_id[pid].get("highlights") or []), default=0)
    return max(answers, key=lambda pid: (max(0, min(10, int(answers[pid].get("lead_score") or 0))), top(pid), by_id[pid]["starred"],
                                         len(by_id[pid]["items"])))


def clean_profile_line(line):
    """A profile line without a bullet or date the model added itself (the date is added by code)."""
    return re.sub(r"^\s*(?:[-*]\s*)?(?:\d{4}-\d{2}-\d{2}\s*[:-]\s*)*", "", line or "").strip()


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
                lines = "".join(f"- {date}: {clean_profile_line(line)}\n" for line in a["profile_lines"] if clean_profile_line(line))
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
        "tldr": [capitalized(b) for b in front["tldr"]], "weather": front["weather"],
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

def wrong_bullet(line, person, input_text):
    """Whether one bullet carries a hard problem: a number or link the input lacks, a link outside the brief, or the wrong "you"."""
    if checks.untraceable_numbers(line, input_text) or checks.unknown_urls(line, input_text):
        return True
    allowed = {u.rstrip(".,;:") for u in brief_urls(person)}
    if any(u.rstrip(".,;:") not in allowed for u in checks.URL_RE.findall(line)):
        return True
    return not person["self"] and checks.second_person(line, person.get("brief", ""))


def repair(answer, person, kind, input_text):
    """The model's answer without the bullets that are wrong, when what is left is still a sound report; else None."""
    lines = answer.get("report_md", "").splitlines()
    kept = [line for line in lines if not (line.startswith("- ") and wrong_bullet(line, person, input_text))]
    if len(kept) == len(lines) or sum(1 for line in kept if line.startswith("- ")) < 2:
        return None
    fixed = {**answer, "report_md": "\n".join(kept) + "\n"}
    return fixed if not hard_problems(check_person(fixed, person, kind, input_text)) else None


def settle(person, kind, answer, problems, input_text=""):
    """The answer to keep for one person: the model's; the model's without its wrong bullets; or, when neither is sound,
    a report built by code from the brief.

    Returns (answer or None, how: None, "repaired" or "fallback").
    """
    if answer is not None and not hard_problems(problems):
        return answer, None
    fixed = repair(answer, person, kind, input_text) if answer is not None and input_text else None
    if fixed is not None:
        return fixed, "repaired"
    fb = fallback_answer(person, kind)
    if fb is None:
        return answer, None
    if answer is not None and answer.get("profile_md") and not hard_problems(checks.check_text("profile_md", answer["profile_md"], person["brief"])):
        fb["profile_md"] = answer["profile_md"]
    return fb, "fallback"


def run_split(runner, prep, previous):
    prompts = runner.prompts
    answers, failed = {}, []
    for p in prep["people"]:
        if p["status"] != "active":
            continue
        req = person_request(prompts, prep, p)
        answer, problems = runner.run(req, lambda a, p=p, req=req: check_person(normalize_answer(a, prep["kind"]), p, prep["kind"], req["prompt"]))
        answer, how = settle(p, prep["kind"], answer, problems, req["prompt"])
        if how:
            runner.calls[-1][how] = True
        if answer is None:
            failed.append(p["id"])
            continue
        answer["report_full"] = report_full(p, prep["kind"], answer["report_md"])
        answers[p["id"]] = answer
    if not answers:
        return answers, None, None, failed
    lead_id = choose_lead(prep, answers)
    req = edition_request(prompts, prep, answers, lead_id, previous)
    front, _ = runner.run(req, lambda a: check_edition(a, req["prompt"], answers[lead_id]["report_md"]))
    if front is None:
        failed.append("edition")
    return answers, front, lead_id, failed


def run_single(runner, prep, previous):
    req = single_request(runner.prompts, prep, previous)
    if not any(p["status"] == "active" for p in prep["people"]):
        return {}, None, None, []
    answer, _ = runner.run(req, lambda a: check_single(a, prep, req["prompt"]))
    by_id = {p["id"]: p for p in prep["people"] if p["status"] == "active"}
    got = {}
    for a in (answer or {}).get("people") or []:
        if isinstance(a, dict) and a.get("id") in by_id and a["id"] not in got:
            got[a["id"]] = dict(a)
    answers, fixed = {}, {"fallback": [], "repaired": []}
    for pid, person in by_id.items():
        a = got.get(pid)
        problems = check_person(normalize_answer(a, prep["kind"]), person, prep["kind"], req["prompt"]) if a is not None else ["missing"]
        a, how = settle(person, prep["kind"], a, problems if a is not None else ["not in the input"], req["prompt"])
        if how:
            fixed[how].append(pid)
        if a is None:
            continue
        a["report_full"] = report_full(person, prep["kind"], a["report_md"])
        answers[pid] = a
    if runner.calls:
        runner.calls[-1]["fallback_ids"], runner.calls[-1]["repaired_ids"] = fixed["fallback"], fixed["repaired"]
    failed = [pid for pid in by_id if pid not in answers]
    if answer is None:
        return answers, None, (choose_lead(prep, answers) if answers else None), failed + ["single"]
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
