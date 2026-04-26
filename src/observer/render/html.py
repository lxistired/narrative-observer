"""Render report HTML + index from collected market data (structured)."""
from datetime import datetime, timezone
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from observer.config import ROOT, SITE_DIR


MARKET_LABELS = {
    "us": ("United States", "美股"),
    "tw": ("Taiwan",        "台股"),
    "kr": ("South Korea",   "韩股"),
}

HEAT_RANK = {"high": 3, "mid": 2, "low": 1}


def _to_list(v) -> list:
    """Coerce to list. None → []. str → [str]. list → list. dict → [dict]."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return v
    return [v]


def _normalize_theme(t: dict) -> dict:
    """Defensive: Grok occasionally returns string where list expected. Coerce."""
    t = dict(t)  # shallow copy
    t["evidence"] = _to_list(t.get("evidence"))
    t["sources"] = _to_list(t.get("sources"))
    raw_tickers = _to_list(t.get("tickers"))
    norm_tickers = []
    for tk in raw_tickers:
        if isinstance(tk, str):
            norm_tickers.append({"code": tk, "name_cn": "", "name_native": ""})
        elif isinstance(tk, dict):
            norm_tickers.append(tk)
    t["tickers"] = norm_tickers
    # boolean-ish coercion
    t["cross_source"] = bool(t.get("cross_source"))
    t["hot_24h"] = bool(t.get("hot_24h"))
    t["hot_7d"]  = bool(t.get("hot_7d"))
    return t


def _build_heat_top(markets: list, top_n: int = 6) -> list:
    """Cross-market top themes for the heat strip. Sort by heat then cross_source."""
    pool = []
    for m in markets:
        for t in m.get("themes", []):
            score = HEAT_RANK.get(t.get("heat", "low"), 1) + (0.5 if t.get("cross_source") else 0)
            pool.append((score, m, t))
    pool.sort(key=lambda x: x[0], reverse=True)
    out = []
    max_score = pool[0][0] if pool else 1
    for score, m, t in pool[:top_n]:
        ticks = " ".join(tk.get("code", "") for tk in (t.get("tickers") or [])[:4])
        out.append({
            "market_code": m["code"],
            "market_label": m["label_cn"],
            "name": t.get("name", ""),
            "ticks": ticks,
            "pct": round(score / max_score, 2),
        })
    return out


def _market_stats(themes: list) -> dict:
    cross = sum(1 for t in themes if t.get("cross_source"))
    seen = set()
    for t in themes:
        for tk in t.get("tickers", []) or []:
            code = tk.get("code")
            if code:
                seen.add(code)
    return {"cross": cross, "tickers": len(seen)}


# ============================================================
# Issue numbering
# ============================================================
def _existing_issue_count() -> int:
    return len(list(SITE_DIR.glob("20*.html")))


def _issue_meta(now: datetime, idx: int) -> dict:
    months_en = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    return {
        "year": now.year,
        "number": idx,
        "date_long": f"{months_en[now.month-1]} {now.day}, {now.year} · {now.strftime('%H:%M')}",
        "date_short": now.strftime("%Y-%m-%d %H:%M"),
        "deck": "今日横扫美、台、韩三地散户公开讨论与资金流向，整理为一张题材热度榜。",
    }


# ============================================================
# Render report (structured themes)
# ============================================================
def render_report(merged_results: dict, raw_summaries: dict, total_cost: float) -> Path:
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(enabled_extensions=("html", "html.j2", "j2"), default=True),
    )
    tpl = env.get_template("report.html.j2")

    now = datetime.now(timezone.utc)
    # HHMMSS to avoid same-minute collisions on rapid manual reruns
    file_ts = now.strftime("%Y-%m-%d_%H%M%S")
    issue_number = _existing_issue_count() + 1

    markets = []
    for code in ["us", "tw", "kr"]:
        if code not in merged_results:
            continue
        m = merged_results[code]
        label_en, label_cn = MARKET_LABELS.get(code, (code.upper(), code))

        meta_bits = []
        if code in raw_summaries:
            for src, info in raw_summaries[code].items():
                if isinstance(info, dict):
                    if "n" in info:
                        meta_bits.append(f"{src}={info['n']}")
                    if "cost" in info:
                        meta_bits.append(f"${info['cost']:.4f}")
        meta = " · ".join(meta_bits) if meta_bits else "merged"

        themes = [_normalize_theme(t) for t in m.get("themes", [])]
        stats = _market_stats(themes)

        markets.append({
            "code": code,
            "label_en": label_en,
            "label_cn": label_cn,
            "meta": meta,
            "themes": themes,
            "stat_cross": stats["cross"],
            "stat_tickers": stats["tickers"],
            # status banners
            "all_sources_failed": bool(m.get("all_sources_failed")),
            "merge_error": m.get("merge_error"),
            "raw_text": m.get("raw_text"),
            "parse_error": m.get("parse_error"),
            "errors": m.get("errors") or [],
            "warnings": m.get("warnings") or [],
            "themes_dropped": m.get("themes_dropped", 0),
        })

    heat_top = _build_heat_top(markets, top_n=6)

    html = tpl.render(
        title=f"Issue No. {issue_number}",
        issue=_issue_meta(now, issue_number),
        markets=markets,
        heat_top=heat_top,
    )

    filename = f"{file_ts}.html"
    out = SITE_DIR / filename
    out.write_text(html, encoding="utf-8")
    print(f"  Report → {out} (cost ${total_cost:.4f})")
    return out


# ============================================================
# Render index
# ============================================================
def render_index() -> Path:
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(enabled_extensions=("html", "html.j2", "j2"), default=True),
    )
    tpl = env.get_template("index.html.j2")

    files = sorted(SITE_DIR.glob("20*.html"), reverse=True)
    total = len(files)
    reports = []
    for i, f in enumerate(files):
        stem = f.stem
        try:
            d, t = stem.split("_")
            # accept HHMM (legacy) or HHMMSS (new)
            hh = t[:2]
            mm = t[2:4]
            ts = f"{d} · {hh}:{mm} UTC"
        except (ValueError, IndexError):
            ts = stem
        number = total - i
        reports.append({
            "filename": f.name,
            "number": f"{number:03d}",
            "title_full": f"Issue No. {number}",
            "timestamp": ts,
        })

    html = tpl.render(reports=reports)
    out = SITE_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"  Index → {out} ({len(reports)} issues)")
    return out
