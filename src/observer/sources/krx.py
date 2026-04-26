"""KRX retail (개인) net-buy data via pykrx."""
import os
from contextlib import contextmanager
from datetime import date, timedelta
from pykrx import stock


PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


@contextmanager
def _no_proxy():
    """Temporarily clear proxy env vars — KRX is reachable directly from CN; clash misroutes it."""
    saved = {k: os.environ.pop(k, None) for k in PROXY_ENV_KEYS}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


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
    d = _last_trading_day()
    try:
        with _no_proxy():
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
