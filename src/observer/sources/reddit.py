"""Reddit .json scraper - 4 US finance subs, hot+top of day."""
import time
import httpx
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential
from observer.config import REDDIT_USER_AGENT


SUBS = ["wallstreetbets", "stocks", "investing", "CryptoCurrency"]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
def _fetch(url: str) -> dict:
    with httpx.Client(timeout=20.0, headers={"User-Agent": REDDIT_USER_AGENT}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.json()


def fetch_sub(sub: str, listing: str = "hot", limit: int = 50) -> list[dict]:
    """Fetch posts from r/{sub}/{listing}.json. listing: hot|top|new|rising."""
    suffix = "?t=day" if listing == "top" else ""
    url = f"https://www.reddit.com/r/{sub}/{listing}.json?limit={limit}{suffix}"
    data = _fetch(url)
    posts = []
    for p in data.get("data", {}).get("children", []):
        d = p["data"]
        posts.append({
            "id": d["id"],
            "subreddit": d["subreddit"],
            "title": d["title"],
            "score": d["score"],
            "num_comments": d["num_comments"],
            "author": d["author"],
            "flair": d.get("link_flair_text") or "",
            "created_utc": d["created_utc"],
            "url": f"https://reddit.com{d['permalink']}",
            "selftext": d.get("selftext", "")[:2000],
        })
    return posts


def fetch_all(window_hours: int = 24) -> dict:
    """Fetch hot+top from all configured subs, filter to recent window."""
    cutoff = (datetime.now() - timedelta(hours=window_hours)).timestamp()
    out = {}
    for sub in SUBS:
        merged = {}
        for listing in ("hot", "top"):
            try:
                for p in fetch_sub(sub, listing, limit=50):
                    if p["created_utc"] >= cutoff:
                        merged[p["id"]] = p
                time.sleep(2)
            except Exception as e:
                print(f"  ! r/{sub} {listing}: {e}")
        out[sub] = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
        print(f"  r/{sub}: {len(out[sub])} posts (window {window_hours}h)")
    return out
