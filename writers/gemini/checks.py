"""Stage 4, in code: the checks every model answer must pass.

Each check returns a list of plain-English problems; an empty list means it
passed. The pipeline sends the problems back to the model for one retry.
"""

import re

CAPS = {"weekly": 250, "update": 120, "edition": 400}
TLDR_WORDS = 15
STORY_WORDS = 30
EM_DASH = "\u2014"

URL_RE = re.compile(r"https?://[^\s<>()\]\"']+")
NUMBER_RE = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*")
WORD_RE = re.compile(r"[\w'][\w'.-]*")
MD_LINK_TARGET = re.compile(r"\]\((\S+?)\)")


def strip_urls(text):
    return URL_RE.sub(" ", MD_LINK_TARGET.sub("]", text or ""))


def words(text):
    """Words a reader sees: link targets and bare addresses do not count, Markdown symbols do not count."""
    return len(WORD_RE.findall(strip_urls(text)))


def numbers(text):
    """The numbers in `text`, outside links, normalized so "07", "7" and "1,000"/"1000" compare equal."""
    out = set()
    for raw in NUMBER_RE.findall(strip_urls(text)):
        plain = raw.replace(",", "")
        try:
            out.add(str(int(plain)) if plain.isdigit() else str(float(plain)))
        except ValueError:
            out.add(plain)
    return out


def all_numbers(text):
    """Like numbers(), but also the numbers inside links (a PR number in its URL may be printed as "#42")."""
    return numbers(text) | {n for u in URL_RE.findall(text or "") for n in numbers(re.sub(r"[/#?=&_.:-]", " ", u))}


def untraceable_numbers(output_text, input_text):
    """Numbers the output prints that appear nowhere in the input."""
    return sorted(numbers(output_text) - all_numbers(input_text), key=lambda s: (len(s), s))


def unknown_urls(output_text, input_text):
    known = set(URL_RE.findall(input_text or ""))
    return sorted({u.rstrip(".,;:") for u in URL_RE.findall(output_text or "")} - {u.rstrip(".,;:") for u in known})


def check_shape(value, schema, path="answer"):
    """A small validator for the response schemas in pipeline.py (OBJECT, ARRAY, STRING, INTEGER, NUMBER)."""
    t = schema.get("type")
    if t == "OBJECT":
        if not isinstance(value, dict):
            return [f"{path} must be an object"]
        problems = [f"{path} is missing {k}" for k in schema.get("required", []) if k not in value]
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                problems += check_shape(value[k], sub, f"{path}.{k}")
        return problems
    if t == "ARRAY":
        if not isinstance(value, list):
            return [f"{path} must be a list"]
        problems = []
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            problems.append(f"{path} needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            problems.append(f"{path} has more than {schema['maxItems']} items")
        for i, v in enumerate(value):
            problems += check_shape(v, schema.get("items", {}), f"{path}[{i}]")
        return problems
    if t == "STRING":
        if not isinstance(value, str):
            return [f"{path} must be text"]
        if schema.get("enum") and value not in schema["enum"]:
            return [f"{path} must be one of {', '.join(schema['enum'])}"]
        return []
    if t == "INTEGER":
        return [] if isinstance(value, int) and not isinstance(value, bool) else [f"{path} must be a whole number"]
    if t == "NUMBER":
        return [] if isinstance(value, (int, float)) and not isinstance(value, bool) else [f"{path} must be a number"]
    return []


def check_text(label, text, input_text, cap=None):
    """Word cap, numbers and links for one piece of written text."""
    problems = []
    if cap is not None and words(text) > cap:
        problems.append(f"{label} has {words(text)} words; the limit is {cap}")
    bad = untraceable_numbers(text, input_text)
    if bad:
        problems.append(f"{label} prints numbers that are not in the input: {', '.join(bad)}; use only numbers given in the input")
    links = unknown_urls(text, input_text)
    if links:
        problems.append(f"{label} links to addresses that are not in the input: {', '.join(links[:5])}; link only to items in the input")
    return problems


YOU_RE = re.compile(r"\b(you|your|yours|yourself|you're|you've|you'll|you'd)\b", re.I)
HYPE_RE = re.compile(r"\b(incredible|incredibly|massive|massively|gorgeous|huge|insane|amazing|stunning|blazing|"
                     r"game[- ]chang\w*|revolution\w*|groundbreaking|epic|smoother than ever|flurry)\b", re.I)
MD_LINK = re.compile(r"\[([^\]]*)\]\((\S+?)\)")
BULLET_WORDS = 25
# Weekly sections whose bullets are commentary rather than items, so they need no link.
UNLINKED_SECTIONS = ("tl;dr", "worth asking", "how you look")


def second_person(text, source=""):
    """Whether the text, outside its links, addresses someone as "you".

    A "you" or "your" inside words quoted from `source` (a repo description such as
    "A tiny currency for your Slack") is the source talking, not the writer, so it does not count.
    """
    plain = MD_LINK.sub(r"\1", strip_urls(text or ""))
    src = " ".join(re.findall(r"[\w']+", (source or "").lower()))
    for m in YOU_RE.finditer(plain):
        line_start = plain.rfind("\n", 0, m.start()) + 1
        line_end = plain.find("\n", m.end())
        before = re.findall(r"[\w']+", plain[line_start:m.start()])[-3:]
        after = re.findall(r"[\w']+", plain[m.end():line_end if line_end >= 0 else None])[:3]
        windows = [" ".join(w.lower() for w in [*before[len(before) - k:], m.group(0), *after[:j]])
                   for k in range(len(before) + 1) for j in range(len(after) + 1) if k + j >= 3]
        if not src or not any(f" {w} " in f" {src} " for w in windows):
            return True
    return False


def copied_runs(text, source, n=8):
    """Runs of `n` words that `text` copies word for word from `source` (links and punctuation ignored)."""
    norm = lambda s: re.findall(r"[\w']+", MD_LINK.sub(r"\1", strip_urls(s or "")).lower())
    src, out = norm(source), []
    grams = {" ".join(src[i:i + n]) for i in range(len(src) - n + 1)}
    words_ = norm(text)
    for i in range(len(words_) - n + 1):
        g = " ".join(words_[i:i + n])
        if g in grams and not any(g in o for o in out):
            out.append(g)
    return out


def hype(text):
    return sorted({m.group(0).lower() for m in HYPE_RE.finditer(strip_urls(text or ""))})


def bullets(md):
    """[(section heading in lowercase, bullet text)] for every top-level "- " bullet of a Markdown body."""
    out, section = [], ""
    for line in (md or "").splitlines():
        if line.startswith("#"):
            section = line.lstrip("#").strip().lower()
        elif line.startswith("- "):
            out.append((section, line[2:].strip()))
    return out


def link_urls(text):
    return [u for _, u in MD_LINK.findall(text or "")]


def words_outside_links(text):
    """Words a bullet says in its own voice: link labels and addresses do not count."""
    return words(MD_LINK.sub(" ", text or ""))


def no_em_dash(value):
    """Replace the em dash everywhere in a JSON-like value (the paper never prints it)."""
    if isinstance(value, str):
        return value.replace(" " + EM_DASH + " ", " - ").replace(EM_DASH, "-")
    if isinstance(value, list):
        return [no_em_dash(v) for v in value]
    if isinstance(value, dict):
        return {k: no_em_dash(v) for k, v in value.items()}
    return value
