"""End-to-end orchestrator. One command runs all sources → merge → render."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import click
from observer.config import DATA_DIR, SITE_DIR, require_xai_key
from observer.sources import xai, reddit, ptt, krx, naver
from observer.synth import merge as synth_merge
from observer.render import html as render_html
from observer.render import feed as render_feed


# ============================================================
# Source summarizers — compact text blocks fed to the LLM
# ============================================================

def _summarize_reddit(by_sub: dict) -> str:
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
        amt_eok = r["net_buy_amount_krw"] / 1e8
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


# ============================================================
# Per-market collection — DRY via secondary-source registry
# ============================================================

def _xai_two_windows(market: str) -> tuple[dict, dict, float]:
    """Run xAI x_search for both 24h and 7d windows. Returns (sources_dict, counts, cost)."""
    print(f"  [{market.upper()}] xAI x_search 24h …")
    x24 = xai.query(market, window_days=1)
    print(f"    cost ${x24['cost_usd']:.4f}, {x24['x_search_calls']} searches")
    print(f"  [{market.upper()}] xAI x_search 7d …")
    x7 = xai.query(market, window_days=7)
    print(f"    cost ${x7['cost_usd']:.4f}, {x7['x_search_calls']} searches")

    sources = {
        "xAI x_search · 24h 窗口 (突变信号)": x24["text"],
        "xAI x_search · 7d 窗口 (本周延续)":  x7["text"],
    }
    counts = {
        "xAI_24h": {"cost": x24["cost_usd"], "n": x24["x_search_calls"]},
        "xAI_7d":  {"cost": x7["cost_usd"],  "n": x7["x_search_calls"]},
    }
    return sources, counts, x24["cost_usd"] + x7["cost_usd"]


# Each entry: (label_in_sources_dict, counts_key, fetcher, summarizer)
SECONDARY_SOURCES = {
    "us": [
        ("Reddit · WSB / stocks / investing / CryptoCurrency (24h)",
         "Reddit",
         lambda: reddit.fetch_all(window_hours=24),
         _summarize_reddit),
    ],
    "tw": [
        ("PTT Stock 板 · 高推文 (24h)",
         "PTT",
         lambda: ptt.fetch_top(min_score=10, max_posts=15),
         _summarize_ptt),
    ],
    "kr": [
        ("KRX 散户(개인)净买入 top 30 · 上一交易日",
         "KRX",
         lambda: krx.fetch_retail_net_buy(top_n=30, market="ALL"),
         _summarize_krx),
        ("Naver 题材当日涨幅榜 top 15",
         "Naver",
         lambda: naver.fetch_themes(top_n=15),
         _summarize_naver),
    ],
}


def collect_market(market: str) -> dict:
    """Run xAI dual-window + secondary sources for a market.

    Returns:
      {
        "sources": {label: text_block, ...},
        "counts":  {key: {n, cost?, error?}, ...},
        "raw_cost": float,
        "errors":  [str, ...],            # human-readable per-source failures
      }
    """
    sources, counts, raw_cost = _xai_two_windows(market)
    errors = []

    for label, key, fetcher, summarizer in SECONDARY_SOURCES.get(market, []):
        print(f"[{market.upper()}] {key} …")
        try:
            data = fetcher()
            sources[label] = summarizer(data)
            n = len(data) if hasattr(data, "__len__") else \
                sum(len(v) for v in data.values()) if isinstance(data, dict) else 0
            counts[key] = {"n": n}
        except Exception as e:
            print(f"  ! {key} failed: {e}")
            counts[key] = {"n": 0, "error": str(e)}
            errors.append(f"{key}: {e}")

    return {"sources": sources, "counts": counts, "raw_cost": raw_cost, "errors": errors}


# ============================================================
# CLI
# ============================================================

@click.group()
def cli():
    """Narrative Observer — multi-market retail theme tracker."""


@cli.command()
@click.option("--markets", default="us,tw,kr", help="Comma-separated: us,tw,kr")
@click.option("--site-url", default=None, envvar="SITE_URL", help="Public URL for RSS")
def run(markets: str, site_url: str | None):
    """Collect → merge → render → write site/."""
    require_xai_key()
    now_utc = datetime.now(timezone.utc)
    today = now_utc.strftime("%Y-%m-%d")
    market_list = [m.strip() for m in markets.split(",") if m.strip()]

    raw = {}
    for m in market_list:
        if m not in SECONDARY_SOURCES:
            print(f"[!] unknown market: {m}")
            continue
        try:
            raw[m] = collect_market(m)
        except Exception as e:
            print(f"[!] {m} collect failed entirely: {e}")
            raw[m] = {"sources": {}, "counts": {"error": str(e)},
                      "raw_cost": 0, "errors": [f"全部数据源不可用: {e}"]}

    # save raw snapshot (UTC timestamp; HHMMSS to avoid collisions on rapid reruns)
    snap_dir = DATA_DIR / today
    snap_dir.mkdir(exist_ok=True)
    snap_ts = now_utc.strftime("%H%M%S")
    (snap_dir / f"raw_{snap_ts}.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # merge per-market with LLM
    market_labels = {"us": "美股", "tw": "台股", "kr": "韩股"}
    merged = {}
    raw_summaries = {}
    total_cost = sum(r.get("raw_cost", 0) for r in raw.values())

    for m, data in raw.items():
        raw_summaries[m] = data["counts"]
        if not data["sources"]:
            # all sources for this market failed — keep market visible with banner
            merged[m] = {
                "market": m,
                "themes": [],
                "all_sources_failed": True,
                "errors": data.get("errors", []),
                "cost_usd": 0,
            }
            print(f"[{m.upper()}] all sources failed — banner only")
            continue

        print(f"[{m.upper()}] LLM merge …")
        try:
            mr = synth_merge.merge_market(
                market=m,
                market_label=market_labels.get(m, m),
                today=today,
                sources=data["sources"],
            )
            mr["errors"] = data.get("errors", [])
            merged[m] = mr
            total_cost += mr.get("cost_usd", 0)
            print(f"  merge cost ${mr.get('cost_usd',0):.4f}, "
                  f"themes {len(mr.get('themes', []))}, dropped {mr.get('themes_dropped', 0)}")
        except Exception as e:
            print(f"  ! merge failed: {e}")
            merged[m] = {
                "market": m,
                "themes": [],
                "merge_error": str(e),
                "errors": data.get("errors", []),
                "cost_usd": 0,
            }

    # persist merged JSON — protects against render failure
    (snap_dir / f"merged_{snap_ts}.json").write_text(
        json.dumps({"merged": merged, "total_cost": total_cost,
                    "raw_summaries": raw_summaries},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # append cost ledger row
    cost_csv = DATA_DIR / "cost.csv"
    if not cost_csv.exists():
        cost_csv.write_text("timestamp_utc,markets,total_cost_usd\n", encoding="utf-8")
    with cost_csv.open("a", encoding="utf-8") as fp:
        fp.write(f"{now_utc.isoformat(timespec='seconds')},"
                 f"{'|'.join(market_list)},{total_cost:.6f}\n")

    # render
    print("[render] HTML …")
    out = render_html.render_report(merged, raw_summaries, total_cost)
    print("[render] index …")
    render_html.render_index()
    print("[render] feed …")
    render_feed.render_feed(site_url=site_url) if site_url else render_feed.render_feed()

    print(f"\n✓ done. total cost ${total_cost:.4f} → {out}")


@cli.command()
@click.option("--reports", is_flag=True, help="Also re-render historical reports from data/<date>/merged_*.json")
def reindex(reports: bool):
    """Rebuild index.html + feed.xml. With --reports, also re-render historical HTML."""
    site_url = os.environ.get("SITE_URL")

    if reports:
        print("[reindex] re-rendering historical reports …")
        count = 0
        for date_dir in sorted(DATA_DIR.glob("20*")):
            for merged_path in sorted(date_dir.glob("merged_*.json")):
                try:
                    payload = json.loads(merged_path.read_text(encoding="utf-8"))
                    merged = payload.get("merged", {})
                    raw_summaries = payload.get("raw_summaries", {})
                    total_cost = payload.get("total_cost", 0)
                    # filename derived from snapshot path: data/2026-04-26/merged_HHMMSS.json
                    # → site/2026-04-26_HHMMSS.html (handled by render_report)
                    render_html.render_report(merged, raw_summaries, total_cost)
                    count += 1
                except Exception as e:
                    print(f"  ! {merged_path}: {e}")
        print(f"  re-rendered {count} reports")

    render_html.render_index()
    render_feed.render_feed(site_url=site_url) if site_url else render_feed.render_feed()


@cli.command()
@click.argument("market")
@click.option("--window", default=7, type=int)
def probe(market: str, window: int):
    """Smoke test single market via xAI only (cheap)."""
    require_xai_key()
    r = xai.query(market, window_days=window)
    print(f"cost ${r['cost_usd']:.4f}, {r['x_search_calls']} searches\n")
    print(r["text"])


if __name__ == "__main__":
    cli()
