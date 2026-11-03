"""미국 ETF·매크로 시세 수집 (yfinance, 실패 시 FDR 폴백).

  - auto_adjust=False 로 원주가 OHLC + Adj Close 를 함께 받아
    adj_factor = Adj Close / Close 로 저장한다.
  - 거래대금은 제공되지 않아 종가×거래량으로 추정(value_is_est=TRUE).
    환율·금리·지수처럼 거래량이 0인 심볼은 거래대금 NULL.
  - 아직 끝나지 않은 오늘 봉은 저장하지 않는다(뉴욕 17시 이전).
"""
from __future__ import annotations

import time
from datetime import datetime

import pandas as pd

NY = "America/New_York"
BATCH = 40


def _cutoff_date() -> pd.Timestamp:
    now = pd.Timestamp.now(tz=NY)
    d = now.normalize()
    return (d if now.hour >= 17 else d - pd.Timedelta(days=1)).tz_localize(None)


def _from_frame(sym_id: str, d: pd.DataFrame, source: str) -> pd.DataFrame:
    d = d.dropna(subset=["Close"])
    d = d[d.index.tz_localize(None) <= _cutoff_date()] if d.index.tz is not None \
        else d[d.index <= _cutoff_date()]
    if d.empty:
        return pd.DataFrame()
    adj = d["Adj Close"] / d["Close"] if "Adj Close" in d else 1.0
    vol = d["Volume"].fillna(0) if "Volume" in d else 0
    value = (d["Close"] * vol).where(vol > 0) if "Volume" in d else None
    return pd.DataFrame({
        "symbol_id": sym_id, "date": pd.to_datetime(d.index).date,
        "open": d["Open"].fillna(d["Close"]), "high": d["High"].fillna(d["Close"]),
        "low": d["Low"].fillna(d["Close"]), "close": d["Close"],
        "adj_factor": adj, "volume": vol, "value": value, "value_is_est": True,
        "source": source, "fetched_at": pd.Timestamp(datetime.now()),
    })


def fetch_yf(symbols: pd.DataFrame, start: str) -> tuple[pd.DataFrame, list[str]]:
    """symbols: columns [id, ticker]. 반환: (시세, 실패한 id 목록)"""
    import yfinance as yf

    frames, failed = [], []
    rows = symbols.to_dict("records")
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        tickers = [r["ticker"] for r in chunk]
        data = yf.download(tickers, start=start, auto_adjust=False, progress=False,
                           threads=4, group_by="ticker", repair=False, timeout=20)
        for r in chunk:
            try:
                d = data[r["ticker"]] if len(tickers) > 1 else data
                f = _from_frame(r["id"], d, "yf")
            except KeyError:
                f = pd.DataFrame()
            (frames.append(f) if len(f) else failed.append(r["id"]))
        time.sleep(1)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), failed


def fetch_fdr(sym_id: str, ticker: str, start: str) -> pd.DataFrame:
    import FinanceDataReader as fdr

    d = fdr.DataReader(ticker, start)
    if d.empty:
        return pd.DataFrame()
    if "Adj Close" not in d:
        d["Adj Close"] = d["Close"]
    for c in ("Open", "High", "Low"):
        if c not in d:
            d[c] = d["Close"]
    f = _from_frame(sym_id, d, "fdr")
    return f
