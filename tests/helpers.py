import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))

import build_site  # noqa: E402
import config  # noqa: E402
import stats  # noqa: E402
from fixture_paper import write_paper  # noqa: E402


def build_fixture(site_extra="", summaries=None):
    """Write the synthetic paper, compute stats, build the site. Returns (tmpdir, paper, out, feed)."""
    tmp = tempfile.TemporaryDirectory()
    paper = write_paper(Path(tmp.name) / "paper", site_extra)
    cfg = config.load(paper)
    snap = stats.build(paper, cfg)
    (paper / "stats").mkdir()
    (paper / "stats" / f"{snap['date']}.json").write_text(json.dumps(snap))
    if summaries:
        (paper / "summaries").mkdir()
        for name, body in summaries.items():
            (paper / "summaries" / name).write_text(json.dumps(body))
    out = Path(tmp.name) / "dist"
    feed = build_site.build(paper, out)
    return tmp, paper, out, feed


def all_html(out):
    return "\n".join(p.read_text() for p in out.rglob("*.html"))
