"""Generate RSS feed.xml from existing site reports."""
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from observer.config import SITE_DIR


SITE_URL = "https://example.github.io/observer"  # overridden via env in CI


def _parse_ts(stem: str) -> datetime:
    try:
        d, t = stem.split("_")
        return datetime.strptime(f"{d}_{t}", "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def render_feed(site_url: str = SITE_URL, max_items: int = 30) -> Path:
    files = sorted(SITE_DIR.glob("20*.html"), reverse=True)[:max_items]

    items_xml = []
    for f in files:
        ts = _parse_ts(f.stem)
        title = f"{ts.strftime('%Y-%m-%d %H:%M')} 散户叙事观测"
        link = f"{site_url}/{f.name}"
        items_xml.append(f"""    <item>
      <title>{title}</title>
      <link>{link}</link>
      <guid isPermaLink="true">{link}</guid>
      <pubDate>{format_datetime(ts)}</pubDate>
      <description>美/台/韩 三市场散户题材热度自动观测报告</description>
    </item>""")

    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>散户叙事观测</title>
    <link>{site_url}</link>
    <description>三市场（美 · 台 · 韩）散户题材热度自动观测</description>
    <language>zh-CN</language>
    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
{chr(10).join(items_xml)}
  </channel>
</rss>
"""
    out = SITE_DIR / "feed.xml"
    out.write_text(body, encoding="utf-8")
    print(f"  Feed → {out} ({len(files)} items)")
    return out
