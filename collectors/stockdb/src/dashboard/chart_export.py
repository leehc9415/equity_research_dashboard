"""종목차트 데이터 → <out>/charts/<code>.js (종목별) + <out>/charts/index.js (검색용 목록)

종목별 파일 (window.DASH_CHART[code]):
  최근 약 3년(760거래일) 일봉 수정주가 OHLC·거래량, RS 점수(IBD식 1~99), 소속 시장 지수 종가.
  주봉·이동평균·RS 라인(종가 / 지수)은 브라우저에서 계산한다.
  d  = 날짜 (첫 날 yyyymmdd 정수, 이후는 직전 거래일과의 일수 차이)
  o h l c = 시가·고가·저가·종가 (유효숫자 6자리)
  v  = 거래량(주, 수정)   r = RS 점수(없으면 null)   ix = 같은 날 지수 종가(코스피/코스닥)

이 파일들은 저장소에 커밋하지 않는다 — 클라우드 갱신이 릴리스(market-charts)에 zip으로 올리고,
사이트 배포 단계가 내려받아 함께 배포한다 (저장소가 매일 수십 MB씩 커지는 것을 막기 위해).
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd

DAYS = 760
BENCH = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}


def _num(a: np.ndarray, n: int = 6) -> list:
    """유효숫자 n자리 숫자 목록 (정수면 int로 — JSON을 짧게)."""
    a = np.asarray(a, dtype=float)
    out = np.full(a.shape, np.nan)
    ok = np.isfinite(a) & (a > 0)
    if ok.any():
        sc = 10.0 ** (n - 1 - np.floor(np.log10(a[ok])))
        out[ok] = np.round(a[ok] * sc) / sc
    return [None if v != v else (int(v) if v.is_integer() else v) for v in out.tolist()]


def build(con, out: Path, verbose: bool = True) -> dict:
    t0 = time.time()
    con.execute("SET enable_progress_bar=false")
    cal = con.execute(f"SELECT date FROM prices_daily WHERE symbol_id='KS11' ORDER BY date DESC LIMIT {DAYS}").df()["date"]
    d0, d1 = cal.min(), cal.max()
    px = con.execute("""
        WITH u AS (SELECT s.id code, s.name, CASE WHEN s.subclass = 'KOSDAQ' THEN 'KOSDAQ' ELSE 'KOSPI' END market,
                          m.se_l, m.se_m
                   FROM symbols s JOIN v_rs_universe v ON v.symbol_id = s.id LEFT JOIN se_sector_map m ON m.code = s.id),
             ix AS (SELECT symbol_id, date, any_value(close) AS ixc FROM prices_daily
                    WHERE symbol_id IN ('KS11', 'KQ11') AND close > 0 GROUP BY 1, 2)
        SELECT p.symbol_id code, p.date, p.open, p.high, p.low, p.close,
               CASE WHEN p.volume > 0 THEN p.volume / p.adj_factor ELSE 0 END vol, p.mcap, r.rs, ix.ixc
        FROM v_prices_adj p JOIN u ON u.code = p.symbol_id
        LEFT JOIN rs_daily r ON r.symbol_id = p.symbol_id AND r.date = p.date
        LEFT JOIN ix ON ix.symbol_id = CASE WHEN u.market = 'KOSDAQ' THEN 'KQ11' ELSE 'KS11' END AND ix.date = p.date
        WHERE p.date BETWEEN ? AND ? AND p.close > 0 AND p.source IN ('marcap', 'naver', 'naver_fix')
        ORDER BY 1, 2""", [d0, d1]).df()
    info = con.execute("""SELECT s.id code, s.name, CASE WHEN s.subclass = 'KOSDAQ' THEN 'KOSDAQ' ELSE 'KOSPI' END market,
                                 m.se_l, m.se_m
        FROM symbols s JOIN v_rs_universe v ON v.symbol_id = s.id LEFT JOIN se_sector_map m ON m.code = s.id""").df()
    info = {r[0]: r[1:] for r in info.itertuples(index=False)}
    t1 = time.time()

    cdir = Path(out) / "charts"
    if cdir.exists():
        shutil.rmtree(cdir)
    cdir.mkdir(parents=True)
    rows, total = [], 0
    codes = px["code"].to_numpy()
    cut = np.flatnonzero(codes[1:] != codes[:-1]) + 1
    starts, ends = np.r_[0, cut], np.r_[cut, len(codes)]
    dts = pd.to_datetime(px["date"]).to_numpy()
    O, H, L, C, V, M, R, X = (px[k].to_numpy(float) for k in ("open", "high", "low", "close", "vol", "mcap", "rs", "ixc"))
    se = lambda v: None if v is None or v != v else v
    for i0, i1 in zip(starts, ends):
        code = codes[i0]
        if i1 - i0 < 20 or code not in info:
            continue
        name, market, sl, sm = info[code]
        d = dts[i0:i1]
        days = (d - d[0]).astype("timedelta64[D]").astype(int)
        dd = np.diff(days, prepend=0).tolist()
        dd[0] = int(pd.Timestamp(d[0]).strftime("%Y%m%d"))
        ixc = pd.Series(X[i0:i1]).ffill().bfill().to_numpy()
        rr = [None if v != v else int(v) for v in R[i0:i1].tolist()]
        obj = {"code": code, "name": name, "market": market, "sector": se(sl), "sub": se(sm), "d": dd,
               "o": _num(O[i0:i1]), "h": _num(H[i0:i1]), "l": _num(L[i0:i1]), "c": _num(C[i0:i1]),
               "v": np.round(V[i0:i1]).astype(np.int64).tolist(), "r": rr, "ix": _num(ixc)}
        body = (f"window.DASH_CHART=window.DASH_CHART||{{}};window.DASH_CHART[{json.dumps(code)}]="
                + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ";\n")
        (cdir / f"{code}.js").write_text(body, encoding="utf-8")
        total += len(body.encode())
        c = C[i0:i1]
        m = M[i1 - 1]
        rows.append([code, name, market, se(sl), se(sm), None if m != m else round(m / 1e8), rr[-1],
                     _num(c[-1:])[0], round((c[-1] / c[-2] - 1) * 100, 2) if len(c) > 1 else None,
                     str(pd.Timestamp(d[-1]).date())])
    rows.sort(key=lambda x: -(x[5] or 0))
    idx = {"asof": str(d1)[:10], "cols": ["code", "name", "market", "sector", "sub", "mcap", "rs", "close", "chg", "date"], "rows": rows}
    body = "window.DASH_CHART_IDX=" + json.dumps(idx, ensure_ascii=False, separators=(",", ":")) + ";\n"
    (cdir / "index.js").write_text(body, encoding="utf-8")
    if verbose:
        print(f"  종목차트: {len(rows)}종목, {(total + len(body)) / 1e6:.1f}MB → {cdir} "
              f"(조회 {t1 - t0:.0f}s, 쓰기 {time.time() - t1:.0f}s)", flush=True)
    return {"n": len(rows), "bytes": total + len(body), "asof": idx["asof"]}


def validate(out: Path, min_count: int = 1500) -> None:
    cdir = Path(out) / "charts"
    txt = (cdir / "index.js").read_text(encoding="utf-8")
    idx = json.loads(txt[len("window.DASH_CHART_IDX="):].rstrip().rstrip(";"))
    if len(idx["rows"]) < min_count:
        raise ValueError(f"Too few chart stocks: {len(idx['rows'])}")
    missing = [r[0] for r in idx["rows"] if not (cdir / f"{r[0]}.js").exists()]
    if missing:
        raise ValueError(f"Missing chart files: {missing[:5]}")
    for code in ("005930", "000660"):
        p = cdir / f"{code}.js"
        if p.exists():
            o = json.loads(p.read_text(encoding="utf-8").split("=", 2)[2].rstrip().rstrip(";"))
            n = len(o["c"])
            if not all(len(o[k]) == n for k in ("d", "o", "h", "l", "v", "r", "ix")) or n < 200:
                raise ValueError(f"Bad chart series: {code}")


if __name__ == "__main__":
    import duckdb
    from .export import SITE
    from ..prices.db import DB_PATH
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else SITE
    con = duckdb.connect(str(DB_PATH), read_only=True)
    build(con, out)
    validate(out)
