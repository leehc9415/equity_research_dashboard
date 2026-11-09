"""일봉 시세 수집 실행기.

    python -m src.prices.run_prices --backfill 5     # 최초 1회: 5년 백필
    python -m src.prices.run_prices                  # 매일: 증분 업데이트
    python -m src.prices.run_prices --only kr        # 한국만 (kr / global)

대상
  한국   KRX 전 종목(유니버스 밖 종목 포함, 범용 DB) + 코스피·코스닥 지수
  해외   symbols 테이블의 미국 ETF·매크로 (yfinance → FDR 폴백)
"""
from __future__ import annotations

import argparse
from datetime import date

import pandas as pd

from . import global_yf, kr, krx_official, naver_kr
from .db import connect, log_run, upsert_prices

GLOBAL_LOOKBACK_DAYS = 10   # 증분 시 최근 N일을 다시 받아 덮어씀 (수정치 반영)


def run_kr(con, backfill_years: int | None):
    """한국 종목.
    과거(~2026-09-11): marcap 연도 파일
    2026-09-14 ~ : 네이버 (fix 구간 보정 → 이후 매일 polling API)
    """
    print("\n[한국] 종목 시세")
    if backfill_years:
        this = date.today().year
        for y in range(this - backfill_years, this + 1):
            n = upsert_prices(con, kr.load_marcap_year(y, refresh=(y == this)))
            print(f"  marcap {y}: {n:,}행")
        print("  ※ 2026-09-14 이후 구간은 `python -m src.prices.naver_kr fix --part i/n` 으로 채우세요")

    # 매일 증분: 최근 거래일 하루치 (네이버 polling API, 16:30 이후)
    last = con.execute("SELECT max(date) FROM prices_daily "
                       "WHERE source IN ('krx','naver','naver_fix')").fetchone()[0]
    td = naver_kr.last_trade_date()
    gap = 0 if last is None else len(pd.bdate_range(pd.Timestamp(last) + pd.Timedelta(days=1), td))
    if gap > 1:
        # 하루 이상 비었으면 그 구간을 종목별 네이버 일봉으로 먼저 채움 (느림: 종목당 0.2초)
        print(f"  {last} 이후 {gap}영업일 비어 있음 → 구간 보정 실행")
        codes = con.execute("SELECT DISTINCT symbol_id FROM prices_daily WHERE date = ? "
                            "AND source IN ('krx','marcap','naver','naver_fix')",
                            [last]).df()["symbol_id"].tolist()
        naver_kr.fix_window(con, codes, str(pd.Timestamp(last) + pd.Timedelta(days=1))[:10])
        kr.recompute_adj_factor(con, codes)
    codes = con.execute("""SELECT DISTINCT symbol_id FROM prices_daily
        WHERE date >= current_date - INTERVAL 30 DAY
          AND source IN ('krx','marcap','naver','naver_fix')""").df()["symbol_id"].tolist()
    q = naver_kr.run_daily(con, sorted(codes))
    # 네이버 값은 시간외 거래가 섞인 임시값 → 아직 교정 안 된 날 + 최근 7영업일을 KRX 정규장 시세로 덮어쓴다
    krx_official.overlay_since_naver(con, sorted(codes), td)

    if backfill_years:
        n = kr.recompute_adj_factor(con)
        print(f"  수정계수 전체 재계산: {n:,}행")
    con.execute("UPDATE prices_daily SET adj_factor = 1.0 WHERE adj_factor IS NULL")
    print(f"  최신일 {con.execute('SELECT max(date) FROM prices_daily WHERE source=?', ['naver']).fetchone()[0]}")

    print("[한국] 지수")
    start = f"{date.today().year - (backfill_years or 0)}-01-01" if backfill_years else \
        (pd.Timestamp.today() - pd.Timedelta(days=GLOBAL_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    idx = kr.load_indices(start)
    upsert_prices(con, idx)
    log_run(con, "kr_indices", "ok" if len(idx) else "fail", idx)
    print(f"  KS11·KQ11 {len(idx)}행")


def run_global(con, backfill_years: int | None):
    print("\n[해외] 미국 ETF·매크로")
    syms = con.execute("""
        SELECT id, ticker, fallback_source, fallback_ticker FROM symbols
        WHERE market IN ('US','GLOBAL') AND source = 'yf' AND active""").df()
    if backfill_years:
        start = f"{date.today().year - backfill_years}-01-01"
    else:
        start = (pd.Timestamp.today() - pd.Timedelta(days=GLOBAL_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    df, failed = global_yf.fetch_yf(syms[["id", "ticker"]], start)
    n = upsert_prices(con, df)
    print(f"  yfinance {df['symbol_id'].nunique() if len(df) else 0}개 심볼, {n:,}행")

    still = []
    for sid in failed:
        fb = syms[syms["id"] == sid].iloc[0]
        if fb["fallback_source"] == "fdr" and fb["fallback_ticker"]:
            try:
                f = global_yf.fetch_fdr(sid, fb["fallback_ticker"], start)
                upsert_prices(con, f)
                print(f"  {sid}: yfinance 실패 → FDR {len(f)}행")
                if f.empty:
                    still.append(sid)
                continue
            except Exception as e:  # noqa: BLE001
                print(f"  {sid}: FDR 폴백 실패 ({e})")
        still.append(sid)
    if still:
        print(f"  [경고] 받지 못한 심볼: {still}")
    log_run(con, "global", "partial" if still else "ok", df, note=",".join(still))


def report(con):
    print("\n[점검] 마지막 날짜가 오래된 심볼 (유니버스 기준)")
    stale = con.execute("""
        WITH last AS (SELECT symbol_id, max(date) d FROM prices_daily GROUP BY 1)
        SELECT s.market, s.id, s.name, l.d
        FROM symbols s LEFT JOIN last l ON l.symbol_id = s.id
        WHERE s.source <> 'derived' AND coalesce(s.origin, '') <> 'krx_all'   -- 상장폐지 종목 제외
          AND (l.d IS NULL OR l.d < current_date - INTERVAL 5 DAY)
        ORDER BY s.market, l.d NULLS FIRST LIMIT 30""").fetchall()
    for r in stale:
        print("  ", r)
    if not stale:
        print("   없음")
    print(con.execute("""
        SELECT source, count(DISTINCT symbol_id) syms, count(*) rows, min(date), max(date)
        FROM prices_daily GROUP BY 1 ORDER BY 1""").df().to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=None, help="N년 백필")
    ap.add_argument("--only", choices=["kr", "global"], default=None)
    a = ap.parse_args()

    con = connect()
    if a.only in (None, "kr"):
        run_kr(con, a.backfill)
    if a.only in (None, "global"):
        run_global(con, a.backfill)
    print("\n[지표·카탈로그]")
    from ..catalog import build_catalog, register_all_kr
    from ..indicators.build import build_all
    register_all_kr(con)      # RS 대상(시장 구분) 판단에 필요 → 지표보다 먼저
    build_all(con)
    build_catalog(con)
    con.execute("CHECKPOINT")
    try:                      # 대시보드 데이터 (site/data) 갱신
        from ..dashboard.export import export
        export(con)
    except Exception as e:  # noqa: BLE001
        print(f"  [경고] 대시보드 내보내기 실패: {e}")
    report(con)
    con.close()


if __name__ == "__main__":
    main()
