"""한국 섹터 지수 (시총가중, 전일 시총 비중으로 매일 리밸런싱).

  섹터 수익률_t = Σ (전일 시총_i × 수익률_i,t) / Σ 전일 시총_i
      수익률_i,t = 수정종가_t / 수정종가_(t-1) - 1  (액면분할·병합에도 지수가 튀지 않음)
      구성 종목 = sector_map 에서 included 인 종목 (현재 섹터 구성을 과거에도 적용)
  지수_t = 100 × Π (1 + 섹터 수익률)    (데이터 첫 거래일 = 100)

결과는 prices_daily 에 symbol_id='KRSEC:<섹터id>', source='sector_idx' 로 저장한다.
  close = 지수, open/high/low = close (장중 값 없음), value = 구성 종목 거래대금 합,
  mcap = 구성 종목 시총 합, volume = 구성 종목 수(참고용)
→ ind_daily·bars_weekly 등 지표가 일반 종목처럼 자동 계산된다.

주의: 현재 구성 종목을 과거에 그대로 적용하므로, 과거 시점엔 없던 종목 구성의
      생존 편향이 있다. 추세 확인용으로는 충분하지만 엄밀한 백테스트용은 아니다.
"""
from __future__ import annotations

BASE = 100.0

SQL = """
CREATE OR REPLACE TEMP TABLE _sec_r AS
WITH m AS (
  SELECT p.symbol_id, p.date, s.sector_id,
         p.close * p.adj_factor AS adj,
         coalesce(p.mcap, p.close * p.shares) AS mcap,
         p.value
  FROM prices_daily p
  JOIN sector_map s ON s.code = p.symbol_id AND s.included
  WHERE p.source IN ('marcap', 'naver', 'naver_fix') AND p.close > 0
)
SELECT sector_id, date, symbol_id, mcap, value,
       adj / lag(adj) OVER w - 1 AS r,
       lag(mcap) OVER w          AS w_prev
FROM m
WINDOW w AS (PARTITION BY symbol_id ORDER BY date);

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
SELECT 'KRSEC:' || sector_id AS symbol_id, date, ret, n_members, mcap, value,
       {base} * exp(sum(ln(1 + coalesce(ret, 0))) OVER (PARTITION BY sector_id ORDER BY date)) AS idx
FROM d;

DELETE FROM prices_daily WHERE source = 'sector_idx';
INSERT INTO prices_daily (symbol_id, date, open, high, low, close, ref_price, adj_factor,
                          volume, value, value_is_est, mcap, shares, source, fetched_at)
SELECT symbol_id, date, idx, idx, idx, idx, NULL, 1.0,
       n_members, value, FALSE, mcap, NULL, 'sector_idx', now()
FROM _sec;
"""


def build_sector_index(con, verbose: bool = True) -> int:
    con.execute(SQL.format(base=BASE))
    n = con.execute("SELECT count(*) FROM _sec").fetchone()[0]
    if verbose:
        s = con.execute("SELECT count(DISTINCT symbol_id), min(date), max(date) FROM _sec").fetchone()
        print(f"  섹터 지수: {s[0]}개 섹터, {n:,}행 ({s[1]} ~ {s[2]})")
    con.execute("DROP TABLE IF EXISTS _sec_r; DROP TABLE IF EXISTS _sec;")
    return n


if __name__ == "__main__":
    from ..prices.db import connect
    c = connect()
    build_sector_index(c)
    c.execute("CHECKPOINT")
    c.close()
