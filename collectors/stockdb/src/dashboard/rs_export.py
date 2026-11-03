"""RS 랭킹 탭 데이터: 섹터 RRG + 종목 RS 표 → site/data/rs.js

섹터 RRG (Relative Rotation Graph)
  대상     한국 섹터 지수 21개 (KRSEC:*), 기준지수 코스피(기본)·코스닥
  주봉     금요일 마감 주봉. 이번 주는 마지막 거래일 종가로 진행 중인 봉을 쓴다.
  RS        = 100 × 섹터 / 기준지수
  RS-Ratio    = EMA3( 100 + (RS − N주 평균) / N주 표준편차 )             기준지수 대비 상대강도의 추세
  RS-Momentum = EMA3( 100 + (Ratio − N주 평균) / N주 표준편차 )          RS-Ratio 가 최근 평균보다 올라가는 중인지
  (평활 없이 주간 변화로 모멘텀을 잡으면 꼬리가 매주 크게 튀어 3주 EMA 평활을 쓴다. 13주 기준 주간 이동폭이 1.34 → 0.55)
  사분면   주도(Ratio≥100, Mom≥100) / 약화(Ratio≥100, Mom<100) / 소외(<100, <100) / 개선(<100, ≥100)
  N        4·8·13·26·52주 (기본 13), 꼬리 8주
  ※ JdK RRG 원 공식은 비공개 → 널리 쓰이는 z-score 근사식.

종목 RS 표 (스탁이지 '종합 RS'와 같은 열)
  RS 대상 전 종목(코스피·코스닥 보통주, 스팩·우선주 제외) + 코스피·코스닥 지수 행
  RS·RS(1M·3M·6M·12M) = rs_daily (IBD식). 지수 행은 같은 공식의 점수를 종목 분포 안에서 백분위로 환산.
  MTT = 미너비니 트렌드 템플릿 8개 조건 모두 충족
    종가>150·200일선, 150일선>200일선, 200일선 1개월 상승, 50일선>150·200일선,
    종가>50일선, 종가 ≥ 52주 저가×1.3, 종가 ≥ 52주 고가×0.75, RS ≥ 70
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOWS = [4, 8, 13, 26, 52]
DEFAULT_N = 13
TAIL = 8
SMOOTH = 3      # RS-Ratio·Momentum 3주 EMA 평활 (꼬리가 튀지 않게)
BENCH = {"KS11": "코스피", "KQ11": "코스닥"}
RS_PERIODS = {"rs_1m": 21, "rs_3m": 63, "rs_6m": 126, "rs_12m": 252}

QUAD = [("주도", 1, 1), ("약화", 1, 0), ("소외", 0, 0), ("개선", 0, 1)]


def quadrant(ratio: float, mom: float) -> str:
    return {(1, 1): "주도", (1, 0): "약화", (0, 0): "소외", (0, 1): "개선"}[(int(ratio >= 100), int(mom >= 100))]


def _weekly(s: pd.Series) -> pd.Series:
    """금요일 마감 주봉 종가 (인덱스 = 그 주 마지막 거래일)."""
    s = s.dropna()
    p = s.index.to_period("W-FRI")
    return s[~p.duplicated(keep="last")]


def rrg_series(sec: pd.Series, bench: pd.Series, n: int, smooth: int = SMOOTH) -> pd.DataFrame:
    df = pd.concat({"s": sec, "b": bench}, axis=1).dropna()
    rs = 100 * df["s"] / df["b"]
    z = lambda x: (x - x.rolling(n).mean()) / x.rolling(n).std()
    ratio = (100 + z(rs)).ewm(span=smooth).mean()
    mom = (100 + z(ratio)).ewm(span=smooth).mean()
    return pd.DataFrame({"ratio": ratio, "mom": mom}).dropna()


def _r2(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 2)


def build_rrg(con) -> dict:
    px = con.execute("""
        SELECT symbol_id, date, close FROM prices_daily
        WHERE (source = 'sector_idx' OR symbol_id IN ('KS11', 'KQ11')) AND close > 0
        ORDER BY 1, 2""").df()
    px["date"] = pd.to_datetime(px["date"])
    daily = {k: g.set_index("date")["close"] for k, g in px.groupby("symbol_id")}
    asof = min(daily["KS11"].index.max(), max(v.index.max() for k, v in daily.items() if k.startswith("KRSEC:")))
    weekly = {k: _weekly(v[v.index <= asof]) for k, v in daily.items()}

    meta = con.execute("""SELECT d.sector_id, d.name, d.sort_order, count(m.code) n
        FROM sector_defs d LEFT JOIN sector_map m ON m.sector_id = d.sector_id AND m.included
        GROUP BY 1, 2, 3 ORDER BY 3""").df()

    weeks = None
    out = {b: {} for b in BENCH}
    sectors = []
    for _, r in meta.iterrows():
        sid = f"KRSEC:{r.sector_id}"
        if sid not in weekly:
            continue
        w = weekly[sid]
        sectors.append({"id": r.sector_id, "name": r["name"].replace("-", "·"), "count": int(r.n),
                        "chg_1w": _r2((w.iloc[-1] / w.iloc[-2] - 1) * 100) if len(w) > 1 else None,
                        "chg_4w": _r2((w.iloc[-1] / w.iloc[-5] - 1) * 100) if len(w) > 4 else None})
        for b in BENCH:
            out[b][r.sector_id] = {}
            for n in WINDOWS:
                rr = rrg_series(w, weekly[b], n).tail(TAIL + 1)
                if weeks is None or len(rr.index) > len(weeks):
                    weeks = rr.index
                out[b][r.sector_id][str(n)] = [[_r2(a), _r2(m)] for a, m in zip(rr["ratio"], rr["mom"])]
    bench_chg = {b: _r2((weekly[b].iloc[-1] / weekly[b].iloc[-2] - 1) * 100) for b in BENCH}
    return {"asof": str(asof.date()), "weeks": [str(d.date()) for d in weeks], "windows": WINDOWS,
            "default_n": DEFAULT_N, "tail": TAIL, "benchmarks": BENCH, "bench_chg_1w": bench_chg,
            "sectors": sectors, "rrg": out}


STOCK_SQL = """
WITH u AS (SELECT symbol_id FROM v_rs_universe UNION ALL SELECT 'KS11' UNION ALL SELECT 'KQ11'),
last AS (SELECT max(date) d FROM prices_daily WHERE source IN ('naver', 'naver_fix', 'marcap')),
p AS (
  SELECT p.symbol_id, p.date, p.close, p.high, p.low, p.mcap
  FROM v_prices_adj p JOIN u USING (symbol_id), last
  WHERE p.date BETWEEN last.d - INTERVAL 700 DAY AND last.d AND p.close > 0
    AND (p.source IN ('marcap', 'naver', 'naver_fix') OR p.symbol_id IN ('KS11', 'KQ11'))),
w AS (
  SELECT *,
    lag(close, 1)   OVER o AS c1,
    lag(close, 21)  OVER o AS c21,  lag(close, 63)  OVER o AS c63,
    lag(close, 126) OVER o AS c126, lag(close, 189) OVER o AS c189, lag(close, 252) OVER o AS c252,
    first_value(close) OVER o AS c0,
    avg(close) OVER (o ROWS 49 PRECEDING)  AS ma50,
    avg(close) OVER (o ROWS 149 PRECEDING) AS ma150,
    avg(close) OVER (o ROWS 199 PRECEDING) AS ma200,
    count(*)   OVER (o ROWS 199 PRECEDING) AS n200,
    max(high)  OVER (o ROWS 249 PRECEDING) AS h52,
    min(low)   OVER (o ROWS 249 PRECEDING) AS l52
  FROM p WINDOW o AS (PARTITION BY symbol_id ORDER BY date)),
x AS (SELECT *, lag(ma200, 21) OVER (PARTITION BY symbol_id ORDER BY date) AS ma200_21 FROM w)
SELECT x.symbol_id, x.close, x.close / x.c1 - 1 AS chg_1d, x.mcap,
       x.close / coalesce(x.c21, x.c0) - 1 AS r_rs_1m,  x.close / coalesce(x.c63, x.c0) - 1 AS r_rs_3m,
       x.close / coalesce(x.c126, x.c0) - 1 AS r_rs_6m, x.close / coalesce(x.c252, x.c0) - 1 AS r_rs_12m,
       CASE WHEN x.c252 > 0 THEN 2 * x.close / x.c63 + x.c63 / x.c126 + x.c126 / x.c189 + x.c189 / x.c252 END AS score,
       (x.n200 = 200 AND x.close > x.ma150 AND x.close > x.ma200 AND x.ma150 > x.ma200
        AND x.ma200 > x.ma200_21 AND x.ma50 > x.ma150 AND x.ma50 > x.ma200 AND x.close > x.ma50
        AND x.close >= 1.3 * x.l52 AND x.close >= 0.75 * x.h52) AS mtt_px
FROM x, last WHERE x.date = last.d
"""


def _pct_of(value, dist: np.ndarray):
    """종목 분포 안에서의 백분위 (rs_daily 규칙: NULL 은 최하위, 동순위 평균, 0~99)."""
    if value is None or not np.isfinite(value):
        return None
    ok = np.isfinite(dist)
    below = (dist[ok] < value).sum() + (~ok).sum()
    ties = (dist[ok] == value).sum()
    return int(min(99, np.floor(100 * (below + 1 + ties / 2) / (len(dist) + 1))))


def build_stocks(con) -> dict:
    f = con.execute(STOCK_SQL).df().set_index("symbol_id")
    asof = con.execute("SELECT max(date) FROM prices_daily WHERE source IN ('naver','naver_fix','marcap')").fetchone()[0]
    rs = con.execute("SELECT symbol_id, rs, rs_1m, rs_3m, rs_6m, rs_12m FROM rs_daily WHERE date = ?",
                     [asof]).df().set_index("symbol_id")
    info = con.execute("""SELECT s.id, s.name, s.subclass AS market, m.sector_id
        FROM symbols s LEFT JOIN sector_map m ON m.code = s.id AND m.included
        WHERE s.id IN (SELECT symbol_id FROM v_rs_universe)""").df().set_index("id")
    stocks = f.drop(index=[i for i in BENCH if i in f.index]).join(rs, how="inner").join(info, how="left")

    def rnd(v, nd=0):
        return None if v is None or pd.isna(v) else (int(round(v)) if nd == 0 else round(float(v), nd))

    rows = []
    for code, r in stocks.iterrows():
        mtt = bool(r.mtt_px) and pd.notna(r.rs) and r.rs >= 70
        rows.append([code, r["name"], r.market, r.sector_id if pd.notna(r.sector_id) else None,
                     rnd(r.close), rnd(r.chg_1d * 100, 2) if pd.notna(r.chg_1d) else None,
                     rnd(r.rs), rnd(r.rs_1m), rnd(r.rs_3m), rnd(r.rs_6m), rnd(r.rs_12m), int(mtt),
                     rnd(r.mcap / 1e8) if pd.notna(r.mcap) else None])

    # 지수 행: 같은 공식의 점수·수익률을 종목 분포 안에서 백분위로
    indices = []
    for b, nm in BENCH.items():
        if b not in f.index:
            continue
        r = f.loc[b]
        row = {"id": b, "name": nm, "close": round(float(r.close), 2),
               "chg_1d": rnd(r.chg_1d * 100, 2) if pd.notna(r.chg_1d) else None,
               "rs": _pct_of(r.score, stocks["score"].to_numpy(dtype=float))}
        for k in RS_PERIODS:
            row[k] = _pct_of(r[f"r_{k}"], stocks[f"r_{k}"].to_numpy(dtype=float))
        indices.append(row)
    return {"asof": str(asof), "indices": indices,
            "cols": ["code", "name", "market", "sector", "close", "chg_1d", "rs", "rs_1m", "rs_3m",
                     "rs_6m", "rs_12m", "mtt", "mcap"],
            "rows": rows}


def build(con) -> dict:
    return {"rrg": build_rrg(con), "stocks": build_stocks(con)}
