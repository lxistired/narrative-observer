"""Naver Finance theme heat scraper."""
import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
}
URL = "https://finance.naver.com/sise/theme.naver"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def _get(url: str) -> str:
    # Bypass system proxy — Naver is reachable directly from CN; clash rules often misroute it.
    with httpx.Client(timeout=20.0, headers=HEADERS, proxy=None, trust_env=False) as c:
        r = c.get(url)
        r.raise_for_status()
        r.encoding = r.encoding or "euc-kr"
        return r.text


def fetch_themes(top_n: int = 15) -> list[dict]:
    """Scrape Naver themes page, return top-gaining themes with member stocks."""
    html = _get(URL)
    soup = BeautifulSoup(html, "lxml")
    themes = []
    rows = soup.select("table.type_1 tr")
    for tr in rows:
        td_name = tr.select_one("td.col_type1 a")
        td_change = tr.select_one("td:nth-of-type(2)")
        if not td_name or not td_change:
            continue
        name = td_name.get_text(strip=True)
        change_text = td_change.get_text(strip=True)
        try:
            change_pct = float(change_text.replace("%", "").replace("+", ""))
        except ValueError:
            continue
        members_tds = tr.select("td.col_type5 a")
        members = [a.get_text(strip=True) for a in members_tds[:5]]
        themes.append({
            "name": name,
            "change_pct": change_pct,
            "leading_members": members,
            "detail_url": "https://finance.naver.com" + td_name["href"] if td_name.get("href") else "",
        })
    themes.sort(key=lambda t: abs(t["change_pct"]), reverse=True)
    print(f"  Naver themes: {len(themes)} 题材, top {min(top_n, len(themes))} returned")
    return themes[:top_n]
