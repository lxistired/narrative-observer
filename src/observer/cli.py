"""End-to-end orchestrator. One command runs all sources → merge → render."""
import json
import time
from datetime import datetime
from pathlib import Path
import click
from observer.config import DATA_DIR, SITE_DIR
from observer.sources import xai, reddit, ptt, krx, naver
from observer.synth import merge as synth_merge
from observer.render import html as render_html
from observer.render import feed as render_feed


def _summarize_reddit(by_sub: dict) -> str:
    """Compact text block for LLM consumption."""
    lines = []
    for sub, posts in by_sub.items():
        lines.append(f"\n## r/{sub} (top {min(15, len(posts))} posts, last 24h)")
        for p in posts[:15]:
            flair = f"[{p['flair']}] " if p['flair'] else ""
            lines.append(f"- [{p['score']}↑ {p['num_comments']}c] {flair}{p['title']}")
            if p['selftext']:
                snippet = p['selftext'][:300].replace("\n", " ")
                lines.append(f"  > {snippet}")
    return "\n".join(lines) if lines else "(no posts)"


def _summarize_ptt(posts: list) -> str:
    if not posts:
        return "(no posts)"
    lines = []
    for p in posts:
        lines.append(f"- [{p['score_raw']}推] {p['date']} {p['title']}")
        if p.get("body"):
            snippet = p["body"][:400].replace("\n", " ")
            lines.append(f"  > {snippet}")
    return "\n".join(lines)


def _summarize_krx(rows: list) -> str:
    if not rows:
        return "(no data)"
    lines = ["散户(개인) 净买入金额排名 top 30 (单位: 韩元):"]
    for i, r in enumerate(rows[:30], 1):
        amt_eok = r["net_buy_amount_krw"] / 1e8  # 亿韩元 단위
        lines.append(f"  {i:>2}. {r['name']} ({r['ticker']}) — 净买入 {amt_eok:,.1f} 亿KRW")
    return "\n".join(lines)


def _summarize_naver(rows: list) -> str:
    if not rows:
        return "(no data)"
    lines = ["Naver 题材当日涨幅榜 top 15 (按 |变动率| 排序):"]
    for i, r in enumerate(rows, 1):
        members = " / ".join(r["leading_members"][:4])
        lines.append(f"  {i:>2}. {r['name']:<24} {r['change_pct']:+.2f}%  代表股: {members}")
    return "\n".join(lines)


def _xai_two_windows(market: str) -> tuple[dict, dict, float, int]:
    """Run xAI x_search for both 24h and 7d windows. Returns (sources_dict, counts, cost, total_calls)."""
    print(f"  [{market.upper()}] xAI x_search 24h …")
    x24 = xai.query(market, window_days=1)
    print(f"    cost ${x24['cost_usd']:.4f}, {x24['x_search_calls']} searches")
    print(f"  [{market.upper()}] xAI x_search 7d …")
    x7 = xai.query(market, window_days=7)
    print(f"    cost ${x7['cost_usd']:.4f}, {x7['x_search_calls']} searches")

    sources = {
        f"xAI x_search · 24h 窗口 (突变信号)": x24["text"],
        f"xAI x_search · 7d 窗口 (本周延续)":  x7["text"],
    }
    cost = x24["cost_usd"] + x7["cost_usd"]
    calls = x24["x_search_calls"] + x7["x_search_calls"]
    return sources, {"xAI_24h": {"cost": x24["cost_usd"], "n": x24["x_search_calls"]},
                     "xAI_7d":  {"cost": x7["cost_usd"],  "n": x7["x_search_calls"]}}, cost, calls


def collect_us() -> dict:
    sources, counts, raw_cost, _ = _xai_two_windows("us")

    print("[US] Reddit (4 subs)…")
    rd = reddit.fetch_all(window_hours=24)
    sources["Reddit · WSB / stocks / investing / CryptoCurrency (24h)"] = _summarize_reddit(rd)
    counts["Reddit"] = {"n": sum(len(v) for v in rd.values())}

    return {"sources": sources, "counts": counts, "raw_cost": raw_cost}


def collect_tw() -> dict:
    sources, counts, raw_cost, _ = _xai_two_windows("tw")

    print("[TW] PTT Stock 板 …")
    try:
        pt = ptt.fetch_top(min_score=10, max_posts=15)
        sources["PTT Stock 板 · 高推文 (24h)"] = _summarize_ptt(pt)
        counts["PTT"] = {"n": len(pt)}
    except Exception as e:
        print(f"  ! PTT failed: {e}")
        counts["PTT"] = {"n": 0, "error": str(e)}

    return {"sources": sources, "counts": counts, "raw_cost": raw_cost}


def collect_kr() -> dict:
    sources, counts, raw_cost, _ = _xai_two_windows("kr")

    print("[KR] KRX 散户净买入 …")
    try:
        kx = krx.fetch_retail_net_buy(top_n=30, market="ALL")
        sources["KRX 散户(개인)净买入 top 30 · 上一交易日"] = _summarize_krx(kx)
        counts["KRX"] = {"n": len(kx)}
    except Exception as e:
        print(f"  ! KRX failed: {e}")
        counts["KRX"] = {"n": 0, "error": str(e)}

    print("[KR] Naver 题材榜 …")
    try:
        nv = naver.fetch_themes(top_n=15)
        sources["Naver 题材当日涨幅榜 top 15"] = _summarize_naver(nv)
        counts["Naver"] = {"n": len(nv)}
    except Exception as e:
        print(f"  ! Naver failed: {e}")
        counts["Naver"] = {"n": 0, "error": str(e)}

    return {"sources": sources, "counts": counts, "raw_cost": raw_cost}


@click.group()
def cli():
    """Narrative Observer — multi-market retail theme tracker."""


@cli.command()
@click.option("--markets", default="us,tw,kr", help="Comma-separated: us,tw,kr")
@click.option("--site-url", default=None, envvar="SITE_URL", help="Public URL for RSS")
def run(markets: str, site_url: str | None):
    """Collect → merge → render → write site/."""
    today = datetime.now().strftime("%Y-%m-%d")
    market_list = [m.strip() for m in markets.split(",") if m.strip()]

    collectors = {"us": collect_us, "tw": collect_tw, "kr": collect_kr}
    raw = {}
    for m in market_list:
        if m not in collectors:
            print(f"[!] unknown market: {m}")
            continue
        try:
            raw[m] = collectors[m]()
        except Exception as e:
            print(f"[!] {m} collect failed: {e}")
            raw[m] = {"sources": {}, "counts": {"error": str(e)}, "raw_cost": 0}

    # save raw snapshot
    snap_dir = DATA_DIR / today
    snap_dir.mkdir(exist_ok=True)
    (snap_dir / f"raw_{datetime.now().strftime('%H%M')}.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # merge per-market with LLM
    market_labels = {"us": "美股", "tw": "台股", "kr": "韩股"}
    merged = {}
    raw_summaries = {}
    total_cost = sum(r.get("raw_cost", 0) for r in raw.values())

    for m, data in raw.items():
        if not data["sources"]:
            continue
        print(f"[{m.upper()}] LLM merge …")
        try:
            mr = synth_merge.merge_market(
                market=m,
                market_label=market_labels.get(m, m),
                today=today,
                sources=data["sources"],
            )
            merged[m] = mr
            total_cost += mr.get("cost_usd", 0)
            print(f"  merge cost ${mr.get('cost_usd',0):.4f}")
        except Exception as e:
            print(f"  ! merge failed: {e}")
            merged[m] = {"merged_text": f"_合并失败: {e}_\n\n原始数据请见快照文件。", "cost_usd": 0}
        raw_summaries[m] = data["counts"]

    # render
    print("[render] HTML …")
    out = render_html.render_report(merged, raw_summaries, total_cost)
    print("[render] index …")
    render_html.render_index()
    print("[render] feed …")
    if site_url:
        render_feed.render_feed(site_url=site_url)
    else:
        render_feed.render_feed()

    print(f"\n✓ done. total cost ${total_cost:.4f} → {out}")


@cli.command()
def reindex():
    """Rebuild index.html + feed.xml from existing site/ files."""
    render_html.render_index()
    render_feed.render_feed()


@cli.command()
@click.argument("market")
@click.option("--window", default=7, type=int)
def probe(market: str, window: int):
    """Smoke test single market via xAI only (cheap)."""
    r = xai.query(market, window_days=window)
    print(f"cost ${r['cost_usd']:.4f}, {r['x_search_calls']} searches\n")
    print(r["text"])


if __name__ == "__main__":
    cli()
