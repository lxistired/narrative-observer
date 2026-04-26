"""KRX retail (개인) net-buy data via pykrx.

KRX (data.krx.co.kr) is reachable directly from CN; Clash typically misroutes it
through proxies that can't reach Korean ranges. Rather than mutating global env
vars (race-prone), we configure the pykrx requests.Session once at import time
to bypass any system proxy.
"""
from datetime import date, timedelta

import requests
from pykrx import stock
from pykrx.website.comm.auth import get_auth_session


def _disable_session_proxy() -> None:
    """Force the pykrx singleton session to ignore system proxy settings.

    Idempotent. Safe to call multiple times. Thread-safe (only mutates session
    attributes, never global env vars).
    """
    krx = get_auth_session()
    if krx is not None and getattr(krx, "session", None) is not None:
        sess: requests.Session = krx.session
        sess.trust_env = False
        sess.proxies = {"http": None, "https": None}


# Apply at import time so first call already bypasses proxy
_disable_session_proxy()


def _last_trading_day() -> str:
    """Find last weekday (KRX is closed weekends; this skips Sat/Sun naively)."""
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def fetch_retail_net_buy(top_n: int = 30, market: str = "ALL") -> list[dict]:
    """Returns top stocks by retail net-buy amount on most recent trading day.

    market: 'KOSPI'|'KOSDAQ'|'ALL'
    """
    # Re-apply in case pykrx rebuilt the session (e.g. token refresh)
    _disable_session_proxy()

    d = _last_trading_day()
    try:
        df = stock.get_market_net_purchases_of_equities(d, d, market, "개인")
    except Exception as e:
        print(f"  ! KRX fetch failed for {d}: {e}")
        return []

    if df is None or df.empty:
        print(f"  ! KRX empty for {d}")
        return []

    df_sorted = df.sort_values("순매수거래대금", ascending=False).head(top_n)

    out = []
    for ticker, row in df_sorted.iterrows():
        out.append({
            "ticker": ticker,
            "name": row.get("종목명", ""),
            "net_buy_amount_krw": int(row.get("순매수거래대금", 0)),
            "net_buy_volume": int(row.get("순매수거래량", 0)),
        })
    print(f"  KRX 散户净买入 top {len(out)} on {d}")
    return out
