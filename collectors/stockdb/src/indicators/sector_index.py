"""섹터 지수 (시총가중, 전일 시총 비중으로 매일 리밸런싱).

  섹터 수익률_t = Σ (전일 시총_i × 수익률_i,t) / Σ 전일 시총_i
      수익률_i,t = 수정종가_t / 수정종가_(t-1) - 1  (액면분할·병합에도 지수가 튀지 않음)
  지수_t = 100 × Π (1 + 섹터 수익률)    (데이터 첫 거래일 = 100)

두 가지 분류로 만든다 (모두 prices_daily 에 저장 → ind_daily·bars 등 지표가 일반 종목처럼 계산됨)
  1) 차트북 섹터 21개   symbol_id 'KRSEC:<섹터id>'   source 'sector_idx'
       구성 = sector_map 에서 included (네이버 업종 → 자체 섹터, 시총 300억↑)
  2) 스탁이지 분류      symbol_id 'SEL:<대분류>' (15개), 'SEM:<중분류>' (47개)   source 'se_sector_idx'
       구성 = se_sector_map (config/universe/stockeasy_sectors.csv, 2026-10-02 구성) 의 전 종목
       RS 랭킹 탭 RRG 에 쓴다.

close = 지수, open/high/low = close (장중 값 없음), value = 구성 종목 거래대금 합,
mcap = 구성 종목 시총 합, volume = 구성 종목 수(참고용)

주의: 현재 구성 종목을 과거에도 그대로 적용하므로 생존 편향이 있다. 추세 확인용.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BASE = 100.0
SE_CSV = Path(__file__).resolve().parents[2] / "config" / "universe" / "stockeasy_sectors.csv"

SQL = """
CREATE OR REPLACE TEMP TABLE _sec_r AS
WITH m AS (
  SELECT p.symbol_id, p.date, s.sector_id,
         p.close * p.adj_factor AS adj,
         coalesce(p.mcap, p.close * p.shares) AS mcap,
         p.value
  FROM prices_daily p
  JOIN ({members}) s ON s.code = p.symbol_id
  WHERE p.source IN ('krx', 'marcap', 'naver', 'naver_fix') AND p.close > 0
)
SELECT sector_id, date, symbol_id, mcap, value,
       adj / lag(adj) OVER w - 1 AS r,
       lag(mcap) OVER w          AS w_prev
FROM m
WINDOW w AS (PARTITION BY symbol_id, sector_id ORDER BY date);

CREATE OR REPLACE TEMP TABLE _sec AS
WITH d AS (
  SELECT sector_id, date,
         sum(w_prev * r) FILTER (WHERE r IS NOT NULL AND w_prev > 0)
           / nullif(sum(w_prev) FILTER (WHERE r IS NOT NULL AND w_prev > 0), 0) AS ret,
         count(*)    AS n_members,
         sum(mcap)   AS mcap,
         sum(value)  AS value
  FROM _sec_r
  GROUP BY 1, 2
)
SELECT sector_id AS symbol_id, date, ret, n_members, mcap, value,
       {base} * exp(sum(ln(1 + coalesce(ret, 0))) OVER (PARTITION BY sector_id ORDER BY date)) AS idx
FROM d;

DELETE FROM prices_daily WHERE source = '{source}';
INSERT INTO prices_daily (symbol_id, date, open, high, low, close, ref_price, adj_factor,
                          volume, value, value_is_est, mcap, shares, source, fetched_at)
SELECT symbol_id, date, idx, idx, idx, idx, NULL, 1.0,
       n_members, value, FALSE, mcap, NULL, '{source}', now()
FROM _sec;
"""

KRSEC_MEMBERS = "SELECT code, 'KRSEC:' || sector_id AS sector_id FROM sector_map WHERE included"
SE_MEMBERS = ("SELECT code, 'SEL:' || se_l AS sector_id FROM se_sector_map "
              "UNION ALL SELECT code, 'SEM:' || se_m AS sector_id FROM se_sector_map")


def load_se_map(con, path: Path = SE_CSV) -> int:
    """스탁이지 분류(대·중분류) 매핑을 se_sector_map 테이블로 (매번 CSV 에서 다시 읽음)."""
    df = pd.read_csv(path, dtype=str)
    con.register("_se", df)
    con.execute("""CREATE OR REPLACE TABLE se_sector_map AS
        SELECT code, name, wi26_l, wi26_m, se_l, se_m FROM _se""")
    con.unregister("_se")
    return len(df)


def _build(con, members: str, source: str) -> int:
    con.execute(SQL.format(members=members, source=source, base=BASE))
    n = con.execute("SELECT count(*) FROM _sec").fetchone()[0]
    con.execute("DROP TABLE IF EXISTS _sec_r; DROP TABLE IF EXISTS _sec;")
    return n


def build_sector_index(con, verbose: bool = True) -> int:
    n = _build(con, KRSEC_MEMBERS, "sector_idx")
    n_se = 0
    if SE_CSV.exists():
        load_se_map(con)
        n_se = _build(con, SE_MEMBERS, "se_sector_idx")
    if verbose:
        s = con.execute("""SELECT source, count(DISTINCT symbol_id), min(date), max(date) FROM prices_daily
                           WHERE source IN ('sector_idx', 'se_sector_idx') GROUP BY 1 ORDER BY 1""").fetchall()
        for src, k, d0, d1 in s:
            print(f"  섹터 지수({src}): {k}개, {d0} ~ {d1}")
    return n + n_se


if __name__ == "__main__":
    from ..prices.db import connect
    c = connect()
    build_sector_index(c)
    c.execute("CHECKPOINT")
    c.close()
