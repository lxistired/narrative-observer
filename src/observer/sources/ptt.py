"""PTT Stock 板 scraper - index page + high-推 post bodies."""
import re
import time
import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential


HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)"}
COOKIES = {"over18": "1"}
BASE = "https://www.ptt.cc"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def _get(url: str) -> str:
    # PTT is fronted by Cloudflare which blocks mainland-CN direct connections.
    # Use system proxy (Clash). In GitHub Actions runner there's no proxy → direct works.
    with httpx.Client(timeout=20.0, headers=HEADERS, cookies=COOKIES) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


def _score(s: str) -> int:
    s = s.strip()
    if not s:
        return 0
    if s == "爆":
        return 100
    if s.startswith("X"):
        try:
            return -int(s[1:])
        except ValueError:
            return -10
    try:
        return int(s)
    except ValueError:
        return 0


def fetch_index(pages: int = 1) -> list[dict]:
    """Fetch latest pages of Stock 板. Returns post metadata sorted by 推数."""
    posts = []
    url = f"{BASE}/bbs/Stock/index.html"
    for _ in range(pages):
        html = _get(url)
        soup = BeautifulSoup(html, "lxml")
        for ent in soup.select("div.r-ent"):
            nrec = ent.select_one("div.nrec")
            link = ent.select_one("div.title a")
            date = ent.select_one("div.date")
            author = ent.select_one("div.meta div.author")
            if not link:
                continue
            score_text = nrec.get_text(strip=True) if nrec else ""
            posts.append({
                "title": link.get_text(strip=True),
                "url": BASE + link["href"],
                "score": _score(score_text),
                "score_raw": score_text,
                "date": date.get_text(strip=True) if date else "",
                "author": author.get_text(strip=True) if author else "",
            })
        prev = soup.select_one("div.btn-group-paging a.btn:nth-of-type(2)")
        if not prev or not prev.get("href"):
            break
        url = BASE + prev["href"]
        time.sleep(1)
    return sorted(posts, key=lambda p: p["score"], reverse=True)


def fetch_post_body(url: str) -> str:
    """Fetch post body text (truncated)."""
    try:
        html = _get(url)
        soup = BeautifulSoup(html, "lxml")
        main = soup.select_one("#main-content")
        if not main:
            return ""
        for sel in main.select(".article-metaline, .article-metaline-right, .push"):
            sel.decompose()
        return main.get_text("\n", strip=True)[:3000]
    except Exception:
        return ""


def fetch_top(min_score: int = 10, max_posts: int = 15) -> list[dict]:
    """Fetch index, then enrich top posts (above min_score) with body text."""
    posts = fetch_index(pages=2)
    top = [p for p in posts if p["score"] >= min_score][:max_posts]
    print(f"  PTT Stock: {len(posts)} 帖 → {len(top)} 高推({min_score}+)")
    for p in top:
        p["body"] = fetch_post_body(p["url"])
        time.sleep(0.5)
    return top
