"""한국 시세 수집: 전 종목 일봉(원주가·거래대금·시총) + 코스피·코스닥 지수.

소스
  백필   FinanceData/marcap  연도별 parquet (1995~, 전 종목 일별, KRX 공식 종가)
  증분   FinanceData/fdr_krx_data_cache  일별 상장종목 스냅샷 CSV (같은 컬럼 구조)
  지수   FinanceDataReader  KS11, KQ11

수정계수
  KRX '전일대비'는 기준가 대비이고, 기준가는 액면분할·무상증자·권리락 때 조정된다.
    기준가_t = 종가_t - 전일대비_t
    r_t      = 기준가_t / 종가_(t-1)        (권리 변동 없는 날 = 1)
    adj_factor_d = Π_{t > d} r_t            (최신일 = 1)
  따라서 종목별 개별 호출 없이 전 종목 수정주가를 계산할 수 있다.
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from ..universe.config import DATA_DIR

MARCAP_URL = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{y}.parquet"
CACHE_URL = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/refs/heads/"
             "master/data/listing/krx/{d}.csv")
RAW_DIR = DATA_DIR / "raw" / "marcap"
KST = "Asia/Seoul"
# marcap 은 이 날짜부터 HTS(KRX 정규장)와 다른 종가를 담고 있다 → 이후 구간은 네이버로 채움
MARCAP_VALID_BEFORE = pd.Timestamp("2026-09-14").date()


def _now_kst() -> pd.Timestamp:
    return pd.Timestamp.now(tz=KST).tz_localize(None)


def normalize(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """marcap / 캐시 CSV → prices_daily 형식."""
    d = df.copy()
    if "ChagesRatio" in d.columns:            # 캐시 CSV의 오타 컬럼명
        d = d.rename(columns={"ChagesRatio": "ChangesRatio"})
    d["Code"] = d["Code"].astype(str).str.zfill(6)
    for c in ("Open", "High", "Low", "Close", "Changes", "Volume", "Amount", "Marcap", "Stocks"):
        if c in d.columns:                    # 캐시 CSV에 가끔 숫자 아닌 값이 섞임
            d[c] = pd.to_numeric(d[c].astype(str).str.replace(",", ""), errors="coerce")
    d = d.dropna(subset=["Close"])
    d["Date"] = pd.to_datetime(d["Date"]).dt.date
    for c in ("Open", "High", "Low"):        # 거래 없는 날은 시·고·저가 0 → 종가로
        d[c] = d[c].where(d[c] > 0, d["Close"])
    out = pd.DataFrame({
        "symbol_id": d["Code"], "date": d["Date"],
        "open": d["Open"], "high": d["High"], "low": d["Low"], "close": d["Close"],
        "ref_price": d["Close"] - d["Changes"],
        "volume": d["Volume"], "value": d["Amount"], "value_is_est": False,
        "mcap": d["Marcap"], "shares": d["Stocks"].astype(float),
        "source": source,
    })
    out["fetched_at"] = pd.Timestamp(datetime.now())
    return out[out["close"] > 0]


def load_marcap_year(year: int, refresh: bool = False) -> pd.DataFrame:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"marcap-{year}.parquet"
    if refresh or not path.exists():
        r = requests.get(MARCAP_URL.format(y=year), timeout=180)
        r.raise_for_status()
        path.write_bytes(r.content)
    df = normalize(pd.read_parquet(path), "marcap")
    return df[df["date"] < MARCAP_VALID_BEFORE]


def load_cache_day(day: pd.Timestamp) -> pd.DataFrame | None:
    """그날 캐시 파일이 없으면(주말·휴장·미생성) None."""
    r = requests.get(CACHE_URL.format(d=day.strftime("%Y-%m-%d")), timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = pd.read_csv(io.StringIO(r.text), index_col=0, dtype={"Code": str})
    d["Date"] = day
    return normalize(d, "krx_cache")


def cache_days_after(last: pd.Timestamp | None, cutoff_hour: int = 18) -> pd.DataFrame:
    """last 다음 날부터 오늘까지 캐시 파일을 모은다. 오늘은 18시(KST) 이후에만."""
    now = _now_kst()
    end = now.normalize() if now.hour >= cutoff_hour else now.normalize() - pd.Timedelta(days=1)
    start = (last + pd.Timedelta(days=1)) if last is not None else end - pd.Timedelta(days=10)
    frames = []
    for day in pd.date_range(start, end, freq="D"):
        if day.weekday() >= 5:
            continue
        f = load_cache_day(day)
        if f is not None:
            frames.append(f)
            print(f"  캐시 {day.date()}: {len(f)}종목")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_indices(start: str) -> pd.DataFrame:
    import FinanceDataReader as fdr

    frames = []
    for sym in ("KS11", "KQ11"):
        d = fdr.DataReader(sym, start)
        if d.empty:
            continue
        d = d.reset_index().rename(columns={d.index.name or "index": "Date"})
        cutoff = _now_kst()
        if cutoff.hour < 16:                     # 장중이면 오늘 봉 제외
            d = d[pd.to_datetime(d["Date"]) < cutoff.normalize()]
        frames.append(pd.DataFrame({
            "symbol_id": sym, "date": pd.to_datetime(d["Date"]).dt.date,
            "open": d["Open"], "high": d["High"], "low": d["Low"], "close": d["Close"],
            "adj_factor": 1.0, "volume": d["Volume"], "value": d.get("Amount"),
            "value_is_est": False, "mcap": d.get("MarCap"), "source": "fdr",
        }))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


ADJ_SQL = """
CREATE OR REPLACE TEMP TABLE _adj AS
WITH r AS (
  SELECT symbol_id, date,
         CASE WHEN ref_price > 0 AND lag(close) OVER w > 0
              THEN ref_price / lag(close) OVER w ELSE 1 END AS r
  FROM prices_daily
  WHERE source IN ('marcap', 'krx', 'krx_cache', 'naver', 'naver_fix') {where}
  WINDOW w AS (PARTITION BY symbol_id ORDER BY date)
)
SELECT symbol_id, date,
       coalesce(exp(sum(ln(r)) OVER (PARTITION BY symbol_id ORDER BY date
                ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING)), 1.0) AS f
FROM r;
UPDATE prices_daily p SET adj_factor = a.f
FROM _adj a WHERE p.symbol_id = a.symbol_id AND p.date = a.date;
"""


def recompute_adj_factor(con, symbols: list[str] | None = None) -> int:
    """KRX 기준가로 수정계수를 다시 계산 (symbols=None 이면 한국 종목 전체)."""
    where = ""
    if symbols:
        con.register("_syms", pd.DataFrame({"s": symbols}))
        where = "AND symbol_id IN (SELECT s FROM _syms)"
    con.execute(ADJ_SQL.format(where=where))
    n = con.execute("SELECT count(*) FROM _adj").fetchone()[0]
    if symbols:
        con.unregister("_syms")
    return n


def changed_factor_symbols(con, new: pd.DataFrame) -> list[str]:
    """새로 들어온 행 중 r != 1 (권리 변동) 이 있는 종목 → 그 종목만 수정계수 재계산."""
    if new.empty:
        return []
    con.register("_n", new[["symbol_id", "date", "ref_price"]])
    rows = con.execute("""
        SELECT DISTINCT n.symbol_id FROM _n n
        JOIN LATERAL (SELECT close FROM prices_daily p
                      WHERE p.symbol_id = n.symbol_id AND p.date < n.date
                      ORDER BY p.date DESC LIMIT 1) prev ON TRUE
        WHERE prev.close > 0 AND abs(n.ref_price / prev.close - 1) > 1e-9
    """).fetchall()
    con.unregister("_n")
    return [r[0] for r in rows]
