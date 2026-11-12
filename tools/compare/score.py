"""Turn reviews into points: parse each review's scores block and apply fixed weights.

A review (tools/compare/REVIEWER.md) ends with a "## Scores" section holding one
fenced JSON block: for each of "Writer A" and "Writer B", the lists of problems
it found (missed news, untraceable facts, wrong numbers, cap overruns, format
errors, tone issues), a readability grade from 1 to 5 and a gut score out of 10.
Points are computed here, in code, from the counts, so two reviewers (or two
runs) are compared on the same scale; the gut score only breaks ties.
"""

import json
import re

BASE = 100
# Points per item found (negative) or per readability grade (positive). Change
# them only together with the table in docs/writers.md and the tests.
WEIGHTS = {
    "missed_news": -6,
    "untraceable_facts": -8,
    "wrong_numbers": -10,
    "cap_overruns": -2,
    "format_errors": -3,
    "tone_issues": -2,
    "readability": 4,
}
COUNTED = [m for m in WEIGHTS if m != "readability"]
LABELS = ("A", "B")
FENCE = re.compile(r"^(`{3,}|~{3,})\s*json\s*\n(.*?)^\1\s*$", re.M | re.S)
LABEL_KEY = re.compile(r"\bWriter ([AB])\)?\s*$")


def _count(metric, value):
    if isinstance(value, bool):
        raise ValueError(f"{metric} must be a list of items, not {value!r}")
    if isinstance(value, list):
        return len(value)
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, dict) and isinstance(value.get("count"), int) and value["count"] >= 0:
        return value["count"]
    raise ValueError(f"{metric} must be a list of items, not {value!r}")


def normalize(raw):
    """One writer's scores block as {metric: count, "readability": 1-5, "gut_score": number or None, "items": {...}}."""
    if not isinstance(raw, dict):
        raise ValueError("each writer's scores must be a JSON object")
    out = {"items": {}}
    for metric in COUNTED:
        if metric not in raw:
            raise ValueError(f"the scores have no {metric}")
        out[metric] = _count(metric, raw[metric])
        out["items"][metric] = [str(x) for x in raw[metric]] if isinstance(raw[metric], list) else []
    r = raw.get("readability")
    if isinstance(r, bool) or not isinstance(r, (int, float)) or not 1 <= r <= 5:
        raise ValueError(f"readability must be a number from 1 to 5, not {r!r}")
    out["readability"] = r
    g = raw.get("gut_score")
    if g is not None and (isinstance(g, bool) or not isinstance(g, (int, float)) or not 0 <= g <= 10):
        raise ValueError(f"gut_score must be a number from 0 to 10, not {g!r}")
    out["gut_score"] = g
    return out


def scores_block(text):
    """The {"A": raw, "B": raw} scores from a review's fenced JSON block. Raises ValueError if there is none."""
    found = None
    for m in FENCE.finditer(text):
        try:
            data = json.loads(m.group(2))
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        labelled = {}
        for k, v in data.items():
            lm = LABEL_KEY.search(k) if isinstance(k, str) else None
            if lm:
                labelled[lm.group(1)] = v
        if set(labelled) == set(LABELS):
            found = labelled
    if found is None:
        raise ValueError('no fenced JSON scores block with "Writer A" and "Writer B" in the review')
    return found


def parse_review(text):
    """{"A": normalized, "B": normalized} from a review's text."""
    raw = scores_block(text)
    out = {}
    for label in LABELS:
        try:
            out[label] = normalize(raw[label])
        except ValueError as e:
            raise ValueError(f"Writer {label}: {e}") from None
    return out


def points(s):
    """BASE plus each count times its weight plus readability times its weight."""
    return BASE + sum(WEIGHTS[m] * s[m] for m in COUNTED) + WEIGHTS["readability"] * s["readability"]


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _fmt(v):
    if v is None:
        return "-"
    return str(int(v)) if float(v).is_integer() else str(v)


def combine(reviews):
    """reviews: {reviewer: parsed}. Returns {label: {"points": {reviewer: n}, "mean", "gut", "metrics": {m: {reviewer: n}}}}."""
    out = {}
    for label in LABELS:
        row = {"points": {}, "gut": {}, "metrics": {m: {} for m in WEIGHTS}}
        for name, parsed in reviews.items():
            s = parsed[label]
            row["points"][name] = points(s)
            row["gut"][name] = s["gut_score"]
            for m in WEIGHTS:
                row["metrics"][m][name] = s[m]
        row["mean"] = _mean(row["points"].values())
        row["gut_mean"] = _mean(row["gut"].values())
        out[label] = row
    return out


def ranking(combined):
    """Labels best first: higher mean points, then higher mean gut score."""
    return sorted(LABELS, key=lambda lb: (-(combined[lb]["mean"] or 0), -(combined[lb]["gut_mean"] or 0), lb))


def weight_table():
    lines = ["| Metric | Points |", "| --- | --- |", f"| Start | {BASE} |"]
    for m, w in WEIGHTS.items():
        each = "per grade (1 to 5)" if m == "readability" else "each"
        lines.append(f"| {m} | {w:+d} {each} |")
    return "\n".join(lines)


def render(reviews, names=None):
    """Markdown tables: points per writer and reviewer, then every metric with the reviewers' spread."""
    names = names or {lb: f"Writer {lb}" for lb in LABELS}
    combined = combine(reviews)
    reviewers = list(reviews)
    head = "| Writer | " + " | ".join(f"{r} reviewer" for r in reviewers) + " | Mean points | Gut score (mean) |"
    lines = [head, "| " + " | ".join(["---"] * (len(reviewers) + 3)) + " |"]
    for lb in ranking(combined):
        c = combined[lb]
        cells = [_fmt(c["points"][r]) for r in reviewers]
        lines.append(f"| {names[lb]} | " + " | ".join(cells) + f" | {_fmt(c['mean'])} | {_fmt(c['gut_mean'])} |")
    best = ranking(combined)
    a, b = combined[best[0]], combined[best[1]]
    if a["mean"] == b["mean"] and a["gut_mean"] == b["gut_mean"]:
        lines += ["", "A tie on points and gut score."]
    else:
        why = "on points" if a["mean"] != b["mean"] else "on the gut score (points tied)"
        lines += ["", f"Ahead {why}: {names[best[0]]}."]
    lines += ["", "Per metric, each reviewer's count (spread is the largest minus the smallest, to show where the reviewers disagree):", ""]
    cols = []
    for lb in LABELS:
        cols += [f"{names[lb]}: {r}" for r in reviewers] + ([f"{names[lb]}: spread"] if len(reviewers) > 1 else [])
    lines += ["| Metric | Weight | " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * (len(cols) + 2)) + " |"]
    for m, w in WEIGHTS.items():
        cells = []
        for lb in LABELS:
            vals = combined[lb]["metrics"][m]
            cells += [_fmt(vals[r]) for r in reviewers]
            if len(reviewers) > 1:
                cells.append(_fmt(max(vals.values()) - min(vals.values())))
        lines.append(f"| {m} | {w:+d} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
