"""지표 엔진: 수정주가(v_prices_adj)로 일·주·월 지표 테이블을 만든다.

만드는 테이블 (매번 전체 재생성 → 권리 변동이 생겨도 항상 일관됨)
  ind_daily     일봉 지표: 이동평균 5·10·20·50·60·120·200일, 거래량 50일 평균,
                52주 고저, 1·3·6·12개월 수익률
  rs_daily      한국 보통주 RS (IBD 방식 종합 RS + 1·3·6·12개월 RS), 날짜별 전 종목 백분위
  bars_weekly   주봉 OHLCV + 10·30·50주 이동평균 + 거래량 10주 평균
  bars_monthly  월봉 OHLCV + 10·12개월 이동평균

규칙
  - 가격은 모두 수정주가. 거래량은 수정 거래량(= 거래량 / adj_factor)으로 분할 전후를 맞춘다.
  - 이동평균은 '거래일 N개' 기준. 데이터가 N개 미만이면 NULL.
  - 52주 고저·기간 수익률은 '달력' 기준(주말 거래되는 크립토도 같은 의미가 되도록).
      high_52w = 최근 365일(당일 포함) 수정 고가의 최대
      ret_1m   = 종가 / (1개월 전 날짜 이전의 마지막 종가) - 1
  - 주봉은 월~일 단위(ISO 주), 월봉은 달력 월. 마지막 봉은 진행 중일 수 있다(is_complete=FALSE).

    python -m src.indicators.build
"""
from __future__ import annotations

import time

from ..prices.db import connect

DAILY_MA = [5, 10, 20, 50, 60, 120, 200]
WEEKLY_MA = [10, 30, 50]
MONTHLY_MA = [10, 12]


def _ma(col: str, n: int, part: str = "symbol_id", order: str = "date") -> str:
    """N개 미만이면 NULL 인 이동평균."""
    w = f"PARTITION BY {part} ORDER BY {order} ROWS BETWEEN {n - 1} PRECEDING AND CURRENT ROW"
    return (f"CASE WHEN count({col}) OVER ({w}) = {n} "
            f"THEN avg({col}) OVER ({w}) END")


def build_daily(con, part: int | None = None, nparts: int = 1):
    """윈도 함수만으로 계산 (ASOF 조인 없이). 종목·날짜 순으로 한 번 정렬해 재사용.

    part/nparts: 메모리·시간이 부족한 PC에서 종목을 nparts 묶음으로 나눠 계산.
      part=0..nparts-1 를 차례로 실행하면 ind_daily_new 에 쌓이고,
      마지막 묶음이 끝나면 ind_daily 로 교체된다.
    """
    ma_cols = ",\n  ".join(f"{_ma('close', n)} AS ma{n}" for n in DAILY_MA)

    def ref(months: int) -> str:   # months 개월 전 날짜 이전의 마지막 종가
        return (f"last_value(close) OVER (PARTITION BY symbol_id ORDER BY date RANGE BETWEEN "
                f"UNBOUNDED PRECEDING AND INTERVAL {months} MONTH PRECEDING)")

    if part is None:
        head, filt = "CREATE OR REPLACE TABLE ind_daily AS", ""
    else:
        head = ("CREATE OR REPLACE TABLE ind_daily_new AS" if part == 0
                else "INSERT INTO ind_daily_new")
        filt = f"WHERE hash(symbol_id) % {nparts} = {part}"

    con.execute(f"""
{head}
WITH d AS (
  SELECT symbol_id, date, close, high, low,
         CASE WHEN volume > 0 THEN volume / adj_factor END AS vol_adj
  FROM v_prices_adj
  {filt}
)
SELECT symbol_id, date,
  {ma_cols},
  {_ma('vol_adj', 50)} AS vol_ma50,
  max(high) OVER w52 AS high_52w,
  min(low)  OVER w52 AS low_52w,
  close / {ref(1)}  - 1 AS ret_1m,
  close / {ref(3)}  - 1 AS ret_3m,
  close / {ref(6)}  - 1 AS ret_6m,
  close / {ref(12)} - 1 AS ret_12m
FROM d
WINDOW w52 AS (PARTITION BY symbol_id ORDER BY date
               RANGE BETWEEN INTERVAL 364 DAY PRECEDING AND CURRENT ROW)
ORDER BY symbol_id, date;
""")
    if part is not None and part == nparts - 1:
        con.execute("DROP TABLE IF EXISTS ind_daily; ALTER TABLE ind_daily_new RENAME TO ind_daily;")


# --------------------------------------------------------------------------- RS
RS_PERIODS = {"rs_1m": 21, "rs_3m": 63, "rs_6m": 126, "rs_12m": 252}   # 거래일
Q = 63                                                                  # IBD 분기


def _pct(x: str) -> str:
    """날짜별 전 종목 백분위 0~99 (동순위는 평균 순위). NULL 은 최하위(0)로 포함."""
    return (f"least(99, floor(100.0 * (rank() OVER (PARTITION BY date ORDER BY {x} NULLS FIRST)"
            f" + (count(*) OVER (PARTITION BY date, {x}) - 1) / 2.0)"
            f" / count(*) OVER (PARTITION BY date)))::INTEGER")


def build_rs(con):
    """IBD 방식 상대강도(RS). 한국 코스피·코스닥 보통주(우선주·스팩·코넥스 제외) 전체 대상.

      rs_1m / rs_3m / rs_6m / rs_12m
          = 21 / 63 / 126 / 252 거래일 수익률의 날짜별 전 종목 백분위(0~99).
            상장 기간이 더 짧으면 상장 후 첫 종가를 기준가로 쓴다.
      rs (종합)
          = 점수 2×(P₀/P₆₃) + P₆₃/P₁₂₆ + P₁₂₆/P₁₈₉ + P₁₈₉/P₂₅₂ 의 백분위 (Pₙ = n 거래일 전 수정종가)
            → 최근 분기 2배 가중. 252거래일 이력이 없으면 0(최하위).
      스탁이지(stockeasy.intellio.kr) 종합 RS 와 같은 공식 (권리 변동 없는 종목 기준 순위 상관 0.9995).
      단, 가격은 정확한 수정주가를 쓴다(스탁이지는 3개월보다 오래된 액면병합·분할을 반영하지 않음).
    """
    lags = sorted(set(RS_PERIODS.values()) | {Q, 2 * Q, 3 * Q, 4 * Q})
    lag_cols = ",\n    ".join(f"lag(p.close, {n}) OVER w AS c{n}" for n in lags)
    ret_cols = ",\n    ".join(f"close / coalesce(c{n}, c0) - 1 AS r_{k}" for k, n in RS_PERIODS.items())
    pct_cols = ",\n  ".join(f"{_pct('r_' + k)} AS {k}" for k in RS_PERIODS)
    con.execute(f"""
CREATE OR REPLACE VIEW v_rs_universe AS
SELECT id AS symbol_id FROM symbols
WHERE market = 'KR' AND asset_class = 'stock' AND subclass IN ('KOSPI', 'KOSDAQ')
  AND right(id, 1) = '0' AND name NOT LIKE '%스팩%';

CREATE OR REPLACE TABLE rs_daily AS
WITH b AS (
  SELECT p.symbol_id, p.date, p.close,
    first_value(p.close) OVER w AS c0,
    {lag_cols}
  FROM v_prices_adj p JOIN v_rs_universe u USING (symbol_id)
  WHERE p.source IN ('marcap', 'naver', 'naver_fix') AND p.close > 0
  WINDOW w AS (PARTITION BY p.symbol_id ORDER BY p.date)
),
r AS (
  SELECT symbol_id, date,
    {ret_cols},
    CASE WHEN c{4 * Q} > 0 THEN 2 * close / c{Q} + c{Q} / c{2 * Q} + c{2 * Q} / c{3 * Q} + c{3 * Q} / c{4 * Q}
    END AS rs_score
  FROM b
)
SELECT symbol_id, date, rs_score,
  CASE WHEN rs_score IS NULL THEN 0 ELSE {_pct('rs_score')} END AS rs,   -- 1년 미만 = 0 (스탁이지와 동일)
  {pct_cols}
FROM r
ORDER BY symbol_id, date;
""")


def _bars(con, table: str, unit: str, mas: list[int], vol_ma: int | None):
    ma_cols = ",\n  ".join(f"{_ma('close', n, order='period_start')} AS ma{n}" for n in mas)
    vol = f",\n  {_ma('volume', vol_ma, order='period_start')} AS vol_ma{vol_ma}" if vol_ma else ""
    con.execute(f"""
CREATE OR REPLACE TABLE {table} AS
WITH b AS (
  SELECT symbol_id,
         date_trunc('{unit}', date)::DATE AS period_start,
         max(date)                         AS date,          -- 그 주/월의 마지막 거래일
         count(*)                          AS n_days,
         arg_min(open, date)               AS open,
         max(high)                         AS high,
         min(low)                          AS low,
         arg_max(close, date)              AS close,
         sum(CASE WHEN volume > 0 THEN volume / adj_factor END) AS volume,
         sum(value)                        AS value
  FROM v_prices_adj
  GROUP BY 1, 2
)
SELECT b.*,
  {ma_cols}{vol},
  (date_trunc('{unit}', current_date) > period_start) AS is_complete
FROM b
ORDER BY symbol_id, period_start;
""")


def build_all(con=None, verbose: bool = True):
    own = con is None
    con = con or connect()
    t = time.time()
    from .sector_index import build_sector_index
    build_sector_index(con, verbose)      # 섹터 지수를 먼저 넣어야 지표가 함께 계산됨
    build_daily(con)
    build_rs(con)
    t1 = time.time()
    _bars(con, "bars_weekly", "week", WEEKLY_MA, 10)
    _bars(con, "bars_monthly", "month", MONTHLY_MA, None)
    if verbose:
        n = [con.execute(f"SELECT count(*) FROM {x}").fetchone()[0]
             for x in ("ind_daily", "bars_weekly", "bars_monthly", "rs_daily")]
        print(f"  지표: ind_daily {n[0]:,} / bars_weekly {n[1]:,} / bars_monthly {n[2]:,} / rs_daily {n[3]:,}행 "
              f"({t1 - t:.0f}s + {time.time() - t1:.0f}s)")
    if own:
        con.execute("CHECKPOINT")
        con.close()


if __name__ == "__main__":
    build_all()
