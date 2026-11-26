"""일봉 시세 수집 실행기.

    python -m src.prices.run_prices --backfill 5     # 최초 1회: 5년 백필
    python -m src.prices.run_prices                  # 매일: 증분 업데이트
    python -m src.prices.run_prices --only kr        # 한국만 (kr / global)

대상
  한국   키움 REST: 현재 상장 코스피·코스닥 주식 + ETF (코넥스·ETN·스팩 제외) + 코스피·코스닥 지수(FDR)
         상장폐지 종목의 과거 시세는 marcap (--backfill)
  해외   symbols 테이블의 미국 ETF·매크로 (yfinance → FDR 폴백)

키움 오늘 봉은 KRX 18:10, NXT 포함(AL) 20:10 이후에 확정 → 매일 실행은 20:10 이후 권장.
"""
from __future__ import annotations

import argparse
from datetime import date

import pandas as pd

from . import global_yf, kiwoom_kr, kr
from .db import connect, log_run, upsert_prices

GLOBAL_LOOKBACK_DAYS = 10   # 증분 시 최근 N일을 다시 받아 덮어씀 (수정치 반영)
KIWOOM_LOOKBACK_DAYS = 7    # 키움 증분: 최근 N일(달력)을 다시 받아 덮어씀 → 며칠 빠져도 메워짐


def run_kr(con, backfill_years: int | None, collect: bool = True):
    """한국 종목. collect=False: 키움 수집 없이(클라우드 — PC 가 올린 키움 테이블을 이미 넣은 상태) 동기화·지수만.
    키움(2019~, 현재 상장) → prices_kiwoom → prices_daily(source='kiwoom')
    marcap 연도 파일(~2026-09-11): 키움에 없는 상장폐지 종목의 과거 시세 (--backfill 때만)
    """
    print("\n[한국] 종목 시세")
    con.execute(kiwoom_kr.DDL)
    if backfill_years:
        this = date.today().year
        for y in range(this - backfill_years, this + 1):
            n = upsert_prices(con, kr.load_marcap_year(y, refresh=(y == this)))
            print(f"  marcap {y}: {n:,}행 (키움이 있는 종목은 아래 동기화에서 다시 키움 값으로 바뀜)")
        kr.recompute_adj_factor(con)

    start = str((pd.Timestamp.today() - pd.Timedelta(days=KIWOOM_LOOKBACK_DAYS)).date())
    try:
        if collect:
            kiwoom_kr.run(con, None, ["KRX", "AL", "REG"], start, skip_done=False, job="kiwoom_daily")
    except Exception as e:  # noqa: BLE001 - 키·네트워크 문제여도 해외·지표 갱신은 계속
        print(f"  [경고] 키움 수집 실패 → 기존 키움 데이터로 진행: {e}")
        log_run(con, "kiwoom_daily", "fail", None, note=str(e)[:500])
    print(f"  prices_daily 동기화: {kiwoom_kr.sync_prices_daily(con):,}행")
    con.execute("UPDATE prices_daily SET adj_factor = 1.0 WHERE adj_factor IS NULL")
    print(f"  최신일 {con.execute('SELECT max(date) FROM prices_daily WHERE source=?', ['kiwoom']).fetchone()[0]}")

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
        SELECT source, count(DISTINCT symbol_id) syms, count(*) n_rows, min(date), max(date)
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
