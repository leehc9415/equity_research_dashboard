"""신고가 탭 데이터 → site/data/nh.js

기간 두 가지 (화면에서 전환)
  52주  직전 250거래일 고가(오늘 제외)   20일  직전 20거래일 고가(오늘 제외)
  모두 수정주가 기준.

판정 (날짜 D, 기간 P)
  신고가        종가 ≥ 직전 P 고가                    (종가 돌파 확정)
  터치 후 밀림  고가 ≥ 직전 P 고가 & 종가 < 직전 P 고가
  후보 단계     고가까지 = 1 − 종가 / 직전 P 고가
                임박 ≤ 3% / 근접 3~7% / 관찰 7~12%   (마지막 날만)
  제외          거래량 0(거래정지)인 날은 판정하지 않음
  태그          21년 이후 최고 = 종가 ≥ DB 전체 기간(2021-01~) 최고 종가, 이력 3년 이상
                신규상장 = 52주 판정 시 이력 250일 미만(60일 이상만 판정)

내보내는 것 (최근 60거래일)
  days    날짜 목록
  rows    (날짜, 종목) 별 스냅샷 — 신고가·터치(52주 또는 20일)였거나, 마지막 날 후보인 경우만
          [day, code, flags, close, chg%, value억, vratio, mcap억, rs, rs1m, dist52%, dist20%, pos52, atr%]
          flags 비트: 1 신고가52 / 2 터치52 / 4 신고가20 / 8 터치20 / 16 21년이후최고 / 32 신규상장
  stocks  종목 정보 {code: [name, market, 대분류, 중분류]}  (스탁이지 분류)
  spark   마지막 날 행이 있는 종목의 최근 60거래일 수정종가 + 직전 52주·20일 고가
  RS·섹터 화면과 같은 유니버스: 코스피·코스닥 보통주(스팩·우선주 제외). 시총·거래대금 하한은 화면에서.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DAYS = 60
CAND_MAX = 0.12

SQL = """
WITH cal AS (SELECT date FROM prices_daily WHERE symbol_id = 'KS11' ORDER BY date DESC LIMIT {days}),
d0 AS (SELECT min(date) AS d, min(date) - INTERVAL 420 DAY AS dw FROM cal),
pre AS (   -- 계산 창 이전 구간의 최고 종가·거래일 수 (21년 이후 최고 태그용)
  SELECT p.symbol_id, max(p.close) AS pm, count(*) AS pn
  FROM v_prices_adj p JOIN v_rs_universe u USING (symbol_id), d0
  WHERE p.date < d0.dw AND p.close > 0 AND p.source IN ('krx', 'marcap', 'naver', 'naver_fix') GROUP BY 1),
p AS (
  SELECT p.symbol_id, p.date, p.open, p.high, p.low, p.close, p.volume, p.value, p.mcap
  FROM v_prices_adj p JOIN v_rs_universe u USING (symbol_id), d0
  WHERE p.date >= d0.dw AND p.close > 0 AND p.source IN ('krx', 'marcap', 'naver', 'naver_fix')),
w AS (
  SELECT *,
    lag(close) OVER o AS c1,
    max(high)  OVER (o ROWS BETWEEN 250 PRECEDING AND 1 PRECEDING) AS h52p,
    count(*)   OVER (o ROWS BETWEEN 250 PRECEDING AND 1 PRECEDING) AS n52,
    max(high)  OVER (o ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)  AS h20p,
    max(high)  OVER (o ROWS 249 PRECEDING) AS h52i,
    min(low)   OVER (o ROWS 249 PRECEDING) AS l52i,
    max(close) OVER (o ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS call_p,
    count(*)   OVER (o ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS n_all,
    avg(value) OVER (o ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS v20p
  FROM p WINDOW o AS (PARTITION BY symbol_id ORDER BY date)),
t AS (
  SELECT *, greatest(high - low, abs(high - c1), abs(low - c1)) AS tr FROM w),
a AS (
  SELECT *, avg(tr) OVER (PARTITION BY symbol_id ORDER BY date ROWS 13 PRECEDING) AS atr FROM t)
SELECT a.symbol_id AS code, a.date, a.high, a.close, a.c1, a.volume, a.value, a.mcap, a.h52p, a.n52, a.h20p, a.h52i, a.l52i,
       greatest(coalesce(pre.pm, 0), coalesce(a.call_p, 0)) AS call_p, coalesce(pre.pn, 0) + a.n_all AS n_all,
       a.v20p, a.atr, r.rs, r.rs_1m
FROM a CROSS JOIN d0 LEFT JOIN pre ON pre.symbol_id = a.symbol_id
LEFT JOIN rs_daily r ON r.symbol_id = a.symbol_id AND r.date = a.date
WHERE a.date >= d0.d
"""


def _f(v, nd=1):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def build(con) -> dict:
    df = con.execute(SQL.format(days=DAYS)).df()
    df["date"] = pd.to_datetime(df["date"])
    days = sorted(df["date"].unique())
    last = days[-1]
    dix = {d: i for i, d in enumerate(days)}

    traded = df["volume"].fillna(0) > 0          # 거래정지(거래량 0)는 판정에서 제외
    ok52 = (df["n52"] >= 60) & traded
    full52 = df["n52"] >= 250
    f = np.zeros(len(df), dtype=int)
    f |= np.where(ok52 & (df.close >= df.h52p), 1, 0)
    f |= np.where(ok52 & (df.high >= df.h52p) & (df.close < df.h52p), 2, 0)
    f |= np.where(traded & (df.close >= df.h20p), 4, 0)
    f |= np.where(traded & (df.high >= df.h20p) & (df.close < df.h20p), 8, 0)
    f |= np.where((df.n_all >= 750) & (df.close >= df.call_p), 16, 0)
    f |= np.where(ok52 & ~full52, 32, 0)
    df["flags"] = f
    df["dist52"] = 1 - df.close / df.h52p
    df["dist20"] = 1 - df.close / df.h20p
    cand = (df["date"] == last) & traded & ((ok52 & (df.dist52 <= CAND_MAX)) | (df.dist20 <= CAND_MAX))
    keep = df[((df["flags"] & 15) > 0) | cand].copy()

    rows = []
    for r in keep.itertuples(index=False):
        rng = r.h52i - r.l52i
        rows.append([dix[r.date], r.code, int(r.flags), _f(r.close, 2),
                     _f((r.close / r.c1 - 1) * 100 if r.c1 else np.nan, 2),
                     _f(r.value / 1e8, 1), _f(r.value / r.v20p if r.v20p else np.nan, 2),
                     None if pd.isna(r.mcap) else int(r.mcap / 1e8),
                     None if pd.isna(r.rs) else int(r.rs), None if pd.isna(r.rs_1m) else int(r.rs_1m),
                     _f(r.dist52 * 100, 2), _f(r.dist20 * 100, 2),
                     _f((r.close - r.l52i) / rng if rng > 0 else np.nan, 3),
                     _f(r.atr / r.close * 100 if r.close else np.nan, 2)])

    codes = sorted(keep["code"].unique())
    info = con.execute("""SELECT s.id, s.name, s.subclass, m.se_l, m.se_m FROM symbols s
        LEFT JOIN se_sector_map m ON m.code = s.id WHERE s.id IN (SELECT unnest(?))""", [codes]).df()
    stocks = {r.id: [r["name"], r.subclass, r.se_l if pd.notna(r.se_l) else None, r.se_m if pd.notna(r.se_m) else None]
              for _, r in info.iterrows()}

    # 마지막 날 행이 있는 종목: 최근 60거래일 종가 + 직전 고가선
    last_codes = sorted(keep.loc[keep["date"] == last, "code"].unique())
    sp = df[df["code"].isin(last_codes)].sort_values(["code", "date"])
    spark = {}
    lastrow = df[df["date"] == last].set_index("code")
    for code, g in sp.groupby("code"):
        c = g.set_index("date")["close"].reindex(days)
        spark[code] = {"c": [None if pd.isna(v) else float(f"{v:.5g}") for v in c],
                       "h52": _f(lastrow.at[code, "h52p"], 2) if code in lastrow.index else None,
                       "h20": _f(lastrow.at[code, "h20p"], 2) if code in lastrow.index else None}

    return {"days": [str(pd.Timestamp(d).date()) for d in days], "asof": str(pd.Timestamp(last).date()),
            "cand_max": CAND_MAX * 100,
            "cols": ["day", "code", "flags", "close", "chg", "value", "vratio", "mcap", "rs", "rs1m",
                     "dist52", "dist20", "pos52", "atr"],
            "rows": rows, "stocks": stocks, "spark": spark}
