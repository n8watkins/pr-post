"""Recent videos from a YouTube channel's public RSS feed. No API key.

    https://www.youtube.com/feeds/videos.xml?channel_id=<UC...>

The feed lists a channel's 15 newest uploads. Everything in it was written by
someone else, so nothing from it is trusted: video ids are checked against
YouTube's id shape and every link is rebuilt from the id rather than copied
from the feed. No dependencies.
"""

import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
MAX_BYTES = 2_000_000
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
TITLE_LIMIT = 300


class FeedError(RuntimeError):
    pass


def clean(text, limit=TITLE_LIMIT):
    """Collapse whitespace, drop control characters, cap the length."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def parse(xml_bytes):
    """Parse a channel feed into {"channel": {...}, "videos": [...]}, newest first."""
    if len(xml_bytes) > MAX_BYTES:
        raise FeedError("feed is too large")
    # Real feeds never declare a DOCTYPE; refusing one rules out entity tricks.
    if b"<!DOCTYPE" in xml_bytes[:2000].upper() or b"<!ENTITY" in xml_bytes.upper():
        raise FeedError("feed declares a DOCTYPE or entities")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise FeedError(f"feed is not valid XML: {e}") from e
    if root.tag != f"{{{NS['atom']}}}feed":
        raise FeedError("not an Atom feed")

    videos = []
    for entry in root.findall("atom:entry", NS):
        vid = (entry.findtext("yt:videoId", "", NS) or "").strip()
        if not VIDEO_ID_RE.match(vid):
            continue
        published = (entry.findtext("atom:published", "", NS) or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}T", published):
            continue
        link = entry.find("atom:link[@rel='alternate']", NS)
        short = link is not None and "/shorts/" in (link.get("href") or "")
        views = None
        stats = entry.find("media:group/media:community/media:statistics", NS)
        if stats is not None and (stats.get("views") or "").isdigit():
            views = int(stats.get("views"))
        videos.append({
            "id": vid,
            "title": clean(entry.findtext("atom:title", "", NS)) or "Untitled video",
            "url": f"https://www.youtube.com/{'shorts/' + vid if short else 'watch?v=' + vid}",
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            "published": published[:10],
            "published_at": published,
            "short": short,
            "views": views,
        })
    videos.sort(key=lambda v: v["published_at"], reverse=True)
    return {"channel": {"title": clean(root.findtext("atom:title", "", NS), 120)}, "videos": videos}


def fetch(channel_id, attempts=3, timeout=30):
    """Download a channel feed, retrying because YouTube's feed endpoint is sometimes flaky."""
    url = FEED.format(channel_id)
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "pr-post (+https://github.com/n8watkins/pr-post)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(MAX_BYTES + 1)
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < attempts - 1:
                time.sleep(5 * (attempt + 1))
    raise FeedError(f"could not fetch {url}: {last}")


def collect(channel_id, since):
    """Videos published on or after `since` (a date), plus the channel's newest few for context."""
    parsed = parse(fetch(channel_id))
    since_iso = since.isoformat()
    return {
        "channel_id": channel_id,
        "channel_url": f"https://www.youtube.com/channel/{channel_id}",
        "channel_title": parsed["channel"]["title"],
        "videos": [v for v in parsed["videos"] if v["published"] >= since_iso],
        "latest": parsed["videos"][:3],
    }
