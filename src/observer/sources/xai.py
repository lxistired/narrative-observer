"""xAI x_search wrapper - one query per market+window returning structured themes."""
import json
import httpx
from datetime import date, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential
from observer.config import XAI_API_KEY, XAI_BASE_URL, XAI_MODEL


PROMPTS = {
    "us": (
        "用中文回答。问：在 {fd} 到 {td} 期间，X 上美股散户讨论最热的 5 个题材/概念是什么？"
        "要求：(1)题材名 (2)一句话叙事 (3)3-5个核心ticker (4)粗略热度信号(高/中/低) "
        "(5)凭啥判定它热(给出几个具体高赞帖证据)。"
        "重要：不预先排除任何ticker（包括meme股），但若属'长期高频死忠在喊但当前没新催化'，明确标'长期meme噪声'并下调热度。"
        "今天是 {today}。"
    ),
    "tw": (
        "用繁体中文回答。问：在 {fd} 到 {td} 期间，X+Threads 上台湾散户讨论最热的 5 个台股题材/概念股是什么？"
        "包括台积电供应链、AI ASIC、半导体上游、生技、储能等。"
        "每个给：题材名、一句话叙事、3-5个核心台股代码（如2330台积电）、热度信号(高/中/低)、几个高赞帖证据（标注台湾账号或繁中帖）。"
        "今天 {today}。"
    ),
    "kr": (
        "用中文回答。问：在 {fd} 到 {td} 期间，X 上韩国散户（包括韩文帖+用英文的韩国账号）讨论最热的 5 个题材/概念股是什么？"
        "包括：(1)韩本土KOSPI/KOSDAQ股 (2)서학개미买的美股 (3)韩国加密。"
        "每个给：题材名、一句话叙事、3-5个核心ticker（韩股写KS代码或韩文公司名）、热度信号(高/中/低)、几个高赞帖证据（标注韩语账号或韩国KOL）。"
        "今天 {today}。"
    ),
}


def _today():
    return date.today().isoformat()


def _date_range(window_days: int):
    today = date.today()
    return (today - timedelta(days=window_days)).isoformat(), today.isoformat()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def query(market: str, window_days: int = 7) -> dict:
    """Query Grok with x_search for one market + window. Returns parsed JSON response."""
    fd, td = _date_range(window_days)
    prompt = PROMPTS[market].format(fd=fd, td=td, today=_today())

    body = {
        "model": XAI_MODEL,
        "input": [{"role": "user", "content": prompt}],
        "tools": [{"type": "x_search", "from_date": fd, "to_date": td}],
    }
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=180.0) as client:
        r = client.post(f"{XAI_BASE_URL}/responses", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()

    text = ""
    for o in data.get("output", []):
        if o.get("type") == "message":
            for c in o.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")

    usage = data.get("usage", {})
    return {
        "market": market,
        "window_days": window_days,
        "from_date": fd,
        "to_date": td,
        "text": text,
        "cost_usd": usage.get("cost_in_usd_ticks", 0) / 1e10,
        "x_search_calls": usage.get("server_side_tool_usage_details", {}).get("x_search_calls", 0),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
