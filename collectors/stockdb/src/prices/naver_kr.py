"""네이버 시세로 한국 종목 일봉 수집·보정.

배경
  marcap 은 2026-09-14부터 HTS(KRX 정규장)와 다른 종가를 담고 있다(그 이전은 100% 일치).
  네이버 시세는 KRX 정규장 종가를 주고, NXT 시간외 가격은 별도 필드로 분리한다.

1) 당일 증분 (매일)
   polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:코드1,코드2,...  (300종목/호출)
   → 시·고·저·종가, 기준가(sv), 거래량, 거래대금, 상장주식수
   장 마감 데이터가 확정되는 16:30(KST) 이후에만 저장한다.

2) 구간 보정 (1회성)
   FDR 네이버 일봉으로 start 이후 OHLC·거래량을 교체.
   거래대금·상장주식수는 marcap 값을 유지(거래량은 네이버와 일치), 시총 = 종가 × 상장주식수.

    python -m src.prices.naver_kr daily
    python -m src.prices.naver_kr fix --start 2026-09-14 --part 1/5
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime

import pandas as pd
import requests

from .db import connect, log_run, upsert_prices

POLL_URL = "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:{codes}"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
BATCH = 300
KST = "Asia/Seoul"


def _now_kst() -> pd.Timestamp:
    return pd.Timestamp.now(tz=KST).tz_localize(None)


def last_trade_date() -> pd.Timestamp:
    """최근 거래일 = 네이버 코스피 지수의 마지막 체결 시각 날짜 (실패 시 FDR 코스피 마지막 날짜).
    FDR 은 당일 봉이 늦게 올라올 수 있어, 저녁 실행 때 오늘 시세에 어제 날짜를 붙이는 사고를 막는다."""
    try:
        r = requests.get("https://m.stock.naver.com/api/index/KOSPI/basic", headers=HEADERS, timeout=15)
        return pd.Timestamp(r.json()["localTradedAt"][:10])
    except Exception:  # noqa: BLE001
        pass
    import FinanceDataReader as fdr
    d = fdr.DataReader("KS11", (_now_kst() - pd.Timedelta(days=15)).strftime("%Y-%m-%d"))
    return pd.Timestamp(d.index.max()).normalize()


def fetch_quotes(codes: list[str], sleep: float = 0.3) -> pd.DataFrame:
    rows = []
    for i in range(0, len(codes), BATCH):
        url = POLL_URL.format(codes=",".join(codes[i:i + BATCH]))
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        for d in r.json()["result"]["areas"][0]["datas"]:
            rows.append(d)
        time.sleep(sleep)
    q = pd.DataFrame(rows)
    out = pd.DataFrame({
        "symbol_id": q["cd"], "open": q["ov"], "high": q["hv"], "low": q["lv"],
        "close": q["nv"], "ref_price": q["sv"], "volume": q["aq"], "value": q["aa"],
        "shares": q["countOfListedStock"].astype(float), "market_status": q["ms"],
    })
    for c in ("open", "high", "low"):          # 거래 없는 종목은 0 → 종가로
        out[c] = out[c].where(out[c] > 0, out["close"])
    out["mcap"] = out["close"] * out["shares"]
    out["value_is_est"] = False
    out["adj_factor"] = 1.0
    out["source"] = "naver"
    out["fetched_at"] = pd.Timestamp(datetime.now())
    return out[out["close"] > 0]


def run_daily(con, codes: list[str]) -> pd.DataFrame:
    now = _now_kst()
    td = last_trade_date()
    if td == now.normalize() and now.hour * 60 + now.minute < 16 * 60 + 30:
        print(f"  오늘({td.date()})은 16:30 이전 → 종가 미확정, 건너뜀")
        return pd.DataFrame()
    q = fetch_quotes(codes)
    # 16:00~18:00 시간외 단일가 동안 market_status 가 OPEN 으로 남지만 nv 는 정규장 종가다
    # (FDR NAVER 일봉 종가와 일치 확인, 2026-11-03). 16:30 가드를 통과했으면 상태와 무관하게 저장.
    q = q.drop(columns="market_status")
    q["date"] = td.date()

    # 주의: polling API 의 sv(전일 기준)는 NXT 포함 통합 종가라 KRX 종가와 다르다.
    #   → 기본은 ref_price = DB의 전일 KRX 종가 (권리 변동 없음, r=1)
    #   → sv 와 전일 종가가 8% 넘게 차이 나는 종목만 의심 종목으로 보고,
    #     네이버 수정주가(FDR)로 실제 권리 변동 비율을 확인한다.
    con.register("_q", q[["symbol_id", "date", "ref_price"]])
    prev = con.execute("""
        SELECT q.symbol_id, q.ref_price AS sv, prev.close AS prev_close, prev.date AS prev_date
        FROM _q q
        JOIN LATERAL (SELECT close, date FROM prices_daily p WHERE p.symbol_id = q.symbol_id
                      AND p.date < q.date ORDER BY p.date DESC LIMIT 1) prev ON TRUE""").df()
    con.unregister("_q")
    prev = prev.set_index("symbol_id")
    q = q.set_index("symbol_id")
    q["ref_price"] = prev["prev_close"].reindex(q.index).fillna(q["ref_price"])

    suspects = prev[(prev["prev_close"] > 0) & ((prev["sv"] / prev["prev_close"] - 1).abs() > 0.08)]
    events = {}
    if len(suspects):
        import FinanceDataReader as fdr
        for sym, row in suspects.iterrows():
            try:
                n = fdr.DataReader("NAVER:" + sym, str(pd.Timestamp(row["prev_date"]).date()))
                adj_prev = float(n["Close"].iloc[0])        # 네이버 수정주가 기준 전일 종가
            except Exception:  # noqa: BLE001
                continue
            r = adj_prev / row["prev_close"]
            if abs(r - 1) > 1e-3:
                events[sym] = r
                q.loc[sym, "ref_price"] = row["prev_close"] * r
    q = q.reset_index()
    upsert_prices(con, q)
    if events:
        from .kr import recompute_adj_factor
        recompute_adj_factor(con, list(events))
    print(f"  네이버 {td.date()}: {len(q)}종목, 의심 {len(suspects)} → 권리 변동 확정 "
          f"{len(events)}종목 {dict(list(events.items())[:5])}")
    log_run(con, "kr_naver_daily", "ok", q, note=",".join(events)[:500])
    return q


def fix_window(con, codes: list[str], start: str) -> int:
    """start 이후 구간의 OHLC 를 네이버 일봉으로 교체 (marcap 오류 구간 보정)."""
    import FinanceDataReader as fdr

    # 네이버 일봉(FDR)은 '오늘 기준 수정주가'다. 그래서
    #   - 구간 안에서는 권리 변동이 이미 반영돼 있으므로 기준가 = 전일 종가 (r = 1)
    #   - 구간 직전일(DB의 원주가)과 이어 붙이는 첫날에만
    #       r = 네이버 수정종가(직전일) / DB 원주가(직전일)
    #     를 넣어, 구간 이후의 모든 권리 변동을 경계에서 한 번에 반영한다.
    fetch_from = str((pd.Timestamp(start) - pd.Timedelta(days=14)).date())   # 경계일 포함
    base = con.execute("""
        SELECT symbol_id, date, close, volume, value, shares
        FROM prices_daily WHERE date >= ? AND symbol_id IN (SELECT unnest(?))""",
                       [fetch_from, codes]).df()
    base["date"] = pd.to_datetime(base["date"]).dt.date
    frames, failed = [], []
    for c in codes:
        time.sleep(0.2)
        try:
            n = fdr.DataReader("NAVER:" + c, fetch_from)
        except Exception:  # noqa: BLE001 - 상장폐지 등
            failed.append(c)
            continue
        if n.empty:
            continue
        n = n.reset_index().rename(columns={"Date": "date"})
        n["date"] = pd.to_datetime(n["date"]).dt.date
        n["symbol_id"] = c
        frames.append(n[["symbol_id", "date", "Open", "High", "Low", "Close", "Volume"]])
    if not frames:
        return 0
    nv = pd.concat(frames, ignore_index=True)
    m = nv.merge(base, on=["symbol_id", "date"], how="left").sort_values(["symbol_id", "date"])
    start_d = pd.Timestamp(start).date()
    # 경계일(구간 직전 거래일)의 DB 원주가와 네이버 수정종가
    edge = m[m["date"] < start_d].groupby("symbol_id").tail(1)
    k = (edge.set_index("symbol_id")["Close"] / edge.set_index("symbol_id")["close"])
    m = m[m["date"] >= start_d].copy()
    m["nv_prev"] = m.groupby("symbol_id")["Close"].shift()
    first = m.groupby("symbol_id")["date"].transform("min") == m["date"]
    m.loc[first, "nv_prev"] = (m.loc[first, "symbol_id"].map(edge.set_index("symbol_id")["close"])
                               * m.loc[first, "symbol_id"].map(k))
    out = pd.DataFrame({
        "symbol_id": m["symbol_id"], "date": m["date"],
        "open": m["Open"].where(m["Open"] > 0, m["Close"]),
        "high": m["High"].where(m["High"] > 0, m["Close"]),
        "low": m["Low"].where(m["Low"] > 0, m["Close"]), "close": m["Close"],
        "ref_price": m["nv_prev"],
        "volume": m["Volume"],
        "value": m["value"].fillna(m["Close"] * m["Volume"]),
        "value_is_est": m["value"].isna(),
        "shares": m.groupby("symbol_id")["shares"].ffill(),
        "source": "naver_fix",
    })
    # 상장주식수가 없는 행(marcap 이 없는 날)은 DB의 가장 최근 값으로
    if out["shares"].isna().any():
        last_sh = con.execute("""SELECT symbol_id, arg_max(shares, date) s FROM prices_daily
            WHERE shares IS NOT NULL AND symbol_id IN (SELECT unnest(?)) GROUP BY 1""",
                              [codes]).df().set_index("symbol_id")["s"]
        out["shares"] = out["shares"].fillna(out["symbol_id"].map(last_sh))
    out["mcap"] = out["close"] * out["shares"]
    out["adj_factor"] = None
    n_up = upsert_prices(con, out)
    if failed:
        print(f"  실패 {len(failed)}종목: {failed[:10]}")
    return n_up


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "fix"])
    ap.add_argument("--start", default="2026-09-14")
    ap.add_argument("--part", default="1/1", help="fix 를 나눠 돌릴 때 i/n")
    ap.add_argument("--codes", default=None, help="fix 대상 종목 (쉼표 구분)")
    a = ap.parse_args()

    con = connect()
    if a.mode == "daily":
        codes = con.execute("""SELECT DISTINCT symbol_id FROM prices_daily
            WHERE source IN ('marcap','naver','naver_fix')
              AND date >= current_date - INTERVAL 30 DAY
              AND regexp_matches(symbol_id, '^[0-9A-Z]{6}$')""").df()["symbol_id"].tolist()
        run_daily(con, sorted(codes))
    else:
        i, n = map(int, a.part.split("/"))
        codes = sorted(con.execute("""SELECT DISTINCT symbol_id FROM prices_daily
            WHERE source = 'marcap' AND date >= ?""", [a.start]).df()["symbol_id"])
        chunk = a.codes.split(",") if a.codes else codes[(i - 1) * len(codes) // n: i * len(codes) // n]
        t = time.time()
        k = fix_window(con, chunk, a.start)
        print(f"  part {a.part}: {len(chunk)}종목, {k:,}행, {time.time() - t:.0f}초")
    con.execute("CHECKPOINT")
    con.close()


if __name__ == "__main__":
    main()
