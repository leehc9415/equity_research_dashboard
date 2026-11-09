"""RS 랭킹 탭 데이터: 섹터 RRG + 종목 RS 표 → site/data/rs.js

섹터 RRG (Relative Rotation Graph) — 토자만 RRG(tojaman.kr/rrg)와 같은 방식
  대상     스탁이지 분류 섹터 지수 — 대분류 15개(SEL:*, 기본 표시) + 중분류 47개(SEM:*, 화면에서 추가)
           시총가중, 기준지수 코스피(기본)·코스닥, 일봉
  기간     4·8·13·26·52주 = 20·40·65·130·260거래일 (기본 13주)
  꼬리     기간을 같은 간격으로 나눈 4·5·6·6·6개 시점 (마지막 점 = 오늘)
  RS          = 섹터 / 기준지수
  RS-Ratio    = 100 + (RS / 최근 f일 RS 평균 − 1) × 100   f = max(15, 기간/2)
                → 기준지수 대비 상대강도가 평소보다 몇 % 높은가
  RS-Momentum = 100 + (RS / p일 전 RS − 1) × 100         p = max(5, 기간/점 수) = 꼬리 한 칸 간격
                → 꼬리 한 칸 동안 상대강도가 몇 % 변했나
  사분면   주도(Ratio≥100, Mom≥100) / 약화(Ratio≥100, Mom<100) / 소외(<100, <100) / 개선(<100, ≥100)

종목 RS 표 (스탁이지 '종합 RS'와 같은 열, 섹터 = 스탁이지 대분류·중분류)
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
PERIODS = {4: (20, 4), 8: (40, 5), 13: (65, 6), 26: (130, 6), 52: (260, 6)}   # 주 → (거래일, 꼬리 점 수)
DEFAULT_N = 13
BENCH = {"KS11": "코스피", "KQ11": "코스닥"}
RS_PERIODS = {"rs_1m": 21, "rs_3m": 63, "rs_6m": 126, "rs_12m": 252}


def quadrant(ratio: float, mom: float) -> str:
    return {(1, 1): "주도", (1, 0): "약화", (0, 0): "소외", (0, 1): "개선"}[(int(ratio >= 100), int(mom >= 100))]


def rrg_points(sec: pd.Series, bench: pd.Series, weeks: int) -> pd.DataFrame:
    """기간(weeks)의 꼬리 점들. index = 날짜, columns = ratio, mom."""
    days, npts = PERIODS[weeks]
    rs = (sec / bench).dropna()
    if len(rs) < 2:
        return pd.DataFrame(columns=["ratio", "mom"])
    l = min(days, len(rs) - 1)
    f, p = max(15, round(l / 2)), max(5, round(l / npts))
    end = len(rs) - 1
    start = end - l
    idx = sorted({round(start + (end - start) * i / (npts - 1)) for i in range(npts)})
    v = rs.to_numpy()
    rows = [(rs.index[i], 100 * v[i] / v[max(0, i - f):i + 1].mean(), 100 * v[i] / v[max(0, i - p)]) for i in idx]
    return pd.DataFrame(rows, columns=["date", "ratio", "mom"]).set_index("date")


def _r2(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 2)


def build_rrg(con) -> dict:
    """이중분류 섹터 지수(대분류 15 'SEL:', 중분류 47 'SEM:')의 RRG."""
    px = con.execute("""
        SELECT symbol_id, date, close FROM prices_daily
        WHERE (source = 'se_sector_idx' OR symbol_id IN ('KS11', 'KQ11')) AND close > 0
        ORDER BY 1, 2""").df()
    px["date"] = pd.to_datetime(px["date"])
    daily = {k: g.set_index("date")["close"] for k, g in px.groupby("symbol_id")}
    asof = min(daily["KS11"].index.max(), max(v.index.max() for k, v in daily.items() if k.startswith("SE")))
    daily = {k: v[v.index <= asof] for k, v in daily.items()}

    # 대분류 순서 = 시총 큰 순, 중분류는 대분류 안에서 시총 큰 순
    meta = con.execute("""
        WITH m AS (SELECT s.se_l, s.se_m, s.code, p.mcap FROM se_sector_map s
                   LEFT JOIN prices_daily p ON p.symbol_id = s.code AND p.date = ?
                     AND p.source IN ('krx', 'marcap', 'naver', 'naver_fix'))
        SELECT 'L' lvl, se_l AS name, NULL AS parent, count(*) n, sum(mcap) cap FROM m GROUP BY se_l
        UNION ALL
        SELECT 'M', se_m, any_value(se_l), count(*), sum(mcap) FROM m GROUP BY se_m""", [asof.date()]).df()
    lcap = meta[meta.lvl == "L"].set_index("name")["cap"]
    meta["pcap"] = meta.apply(lambda r: lcap.get(r.parent if r.lvl == "M" else r["name"], 0), axis=1)
    meta = meta.sort_values(["pcap", "lvl", "cap"], ascending=[False, True, False])

    def chg(s, n):
        return _r2((s.iloc[-1] / s.iloc[-1 - n] - 1) * 100) if len(s) > n else None

    out = {b: {} for b in BENCH}
    dates = {b: {} for b in BENCH}
    sectors = []
    for _, r in meta.iterrows():
        sid = ("SEL:" if r.lvl == "L" else "SEM:") + r["name"]
        if sid not in daily:
            continue
        s = daily[sid]
        sectors.append({"id": sid, "name": r["name"], "level": r.lvl, "parent": r.parent if r.lvl == "M" else None,
                        "count": int(r.n), "mcap": int(r.cap / 1e8) if pd.notna(r.cap) else None,
                        "chg_1w": chg(s, 5), "chg_4w": chg(s, 20)})
        for b in BENCH:
            j = pd.concat({"s": s, "b": daily[b]}, axis=1).dropna()
            out[b][sid] = {}
            for w in WINDOWS:
                pts = rrg_points(j["s"], j["b"], w)
                out[b][sid][str(w)] = [[_r2(a), _r2(m)] for a, m in zip(pts["ratio"], pts["mom"])]
                dates[b].setdefault(str(w), [str(d.date()) for d in pts.index])
    return {"asof": str(asof.date()), "windows": WINDOWS, "default_n": DEFAULT_N,
            "periods": {str(k): {"days": v[0], "points": v[1]} for k, v in PERIODS.items()},
            "dates": dates, "benchmarks": BENCH, "classification": "이중분류(대분류 15 · 중분류 47, 2026-10-02 구성)",
            "bench_chg_1w": {b: chg(daily[b], 5) for b in BENCH},
            "sectors": sectors, "rrg": out}


STOCK_SQL = """
WITH u AS (SELECT symbol_id FROM v_rs_universe UNION ALL SELECT 'KS11' UNION ALL SELECT 'KQ11'),
last AS (SELECT max(date) d FROM prices_daily WHERE source IN ('krx', 'naver', 'naver_fix', 'marcap')),
p AS (
  SELECT p.symbol_id, p.date, p.close, p.high, p.low, p.mcap
  FROM v_prices_adj p JOIN u USING (symbol_id), last
  WHERE p.date BETWEEN last.d - INTERVAL 700 DAY AND last.d AND p.close > 0
    AND (p.source IN ('krx', 'marcap', 'naver', 'naver_fix') OR p.symbol_id IN ('KS11', 'KQ11'))),
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
    asof = con.execute("SELECT max(date) FROM prices_daily WHERE source IN ('krx','naver','naver_fix','marcap')").fetchone()[0]
    rs = con.execute("SELECT symbol_id, rs, rs_1m, rs_3m, rs_6m, rs_12m FROM rs_daily WHERE date = ?",
                     [asof]).df().set_index("symbol_id")
    info = con.execute("""SELECT s.id, s.name, s.subclass AS market, m.se_l AS sector_id, m.se_m AS sub
        FROM symbols s LEFT JOIN se_sector_map m ON m.code = s.id
        WHERE s.id IN (SELECT symbol_id FROM v_rs_universe)""").df().set_index("id")
    stocks = f.drop(index=[i for i in BENCH if i in f.index]).join(rs, how="inner").join(info, how="left")

    def rnd(v, nd=0):
        return None if v is None or pd.isna(v) else (int(round(v)) if nd == 0 else round(float(v), nd))

    rows = []
    for code, r in stocks.iterrows():
        mtt = bool(r.mtt_px) and pd.notna(r.rs) and r.rs >= 70
        rows.append([code, r["name"], r.market, r.sector_id if pd.notna(r.sector_id) else None,
                     r["sub"] if pd.notna(r["sub"]) else None,
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
            "cols": ["code", "name", "market", "sector", "sub", "close", "chg_1d", "rs", "rs_1m", "rs_3m",
                     "rs_6m", "rs_12m", "mtt", "mcap"],
            "rows": rows}


def build(con) -> dict:
    return {"rrg": build_rrg(con), "stocks": build_stocks(con)}


PX_DAYS = 420   # 사용자 정의 섹터용: 52주 기간(260일) + RS 평균 창(130일) + 여유


def build_px(con) -> dict:
    """사용자 정의 섹터(브라우저에서 종목을 골라 만드는 바스켓)를 계산하기 위한 종목 일간 수익률.

    파일 크기를 줄이려고 가격 대신 일간 수익률(bp, 정수)을 보낸다.
      r[i][t] = 종목 i 의 t일 수익률 × 10000 (수정주가 기준), 거래 없으면 null
      mcap[i] = 마지막 날 시총(억) → 브라우저가 수익률을 거꾸로 누적해 날짜별 비중을 복원(현재 주식수 기준)
    기준지수(코스피·코스닥)도 같은 형식.
    """
    dates = con.execute(f"""SELECT date FROM prices_daily WHERE symbol_id = 'KS11'
        ORDER BY date DESC LIMIT {PX_DAYS + 1}""").df()["date"].sort_values()
    d0, d1 = dates.iloc[0], dates.iloc[-1]
    px = con.execute("""
        SELECT p.symbol_id, p.date, p.close, p.mcap
        FROM v_prices_adj p
        WHERE p.date BETWEEN ? AND ? AND p.close > 0
          AND (p.symbol_id IN ('KS11', 'KQ11')
               OR (p.symbol_id IN (SELECT symbol_id FROM v_rs_universe) AND p.source IN ('krx', 'marcap', 'naver', 'naver_fix')))
        ORDER BY 1, 2""", [d0, d1]).df()
    px["date"] = pd.to_datetime(px["date"])
    cal = pd.DatetimeIndex(pd.to_datetime(dates))
    codes, mcaps, rets = [], [], []
    bench = {}
    for sid, g in px.groupby("symbol_id"):
        c = g.set_index("date")["close"].reindex(cal)
        r = (c / c.shift(1) - 1) * 1e4
        arr = [None if pd.isna(v) else int(round(v)) for v in r.iloc[1:]]
        if sid in BENCH:
            bench[sid] = arr
            continue
        m = g["mcap"].iloc[-1]
        if pd.isna(m) or g["date"].iloc[-1] != cal[-1]:
            continue
        codes.append(sid); mcaps.append(int(m / 1e8)); rets.append(arr)
    return {"dates": [str(d.date()) for d in cal[1:]], "bench": bench, "codes": codes, "mcap": mcaps, "r": rets}
