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

가격 기준 (2026-11-14)
  모든 한국 종목 시세는 KRX 정규장 기준(marcap·KRX 캐시)이다.
  네이버 일별 시세의 시·고·저·종가는 NXT 거래(08:00 프리마켓~20:00 애프터마켓)가 섞인
  통합 시세라 쓰지 않는다. 2026-09-14~11-13 구간에 들어갔던 네이버 값은 KRX 값으로 교체했다
  (검증: 스탁이지 화면 현재가 138건 = KRX 정규장 종가 138건, 네이버 종가와는 12%만 일치).
  KRX 캐시는 당일 장중(13~14시)에도 한 번 갱신되므로, 당일 파일은 저녁 갱신 커밋(18시 이후)이
  확인될 때만 쓴다.
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
CACHE_COMMITS_API = ("https://api.github.com/repos/FinanceData/fdr_krx_data_cache/commits"
                     "?path=data/listing/krx/{d}.csv&per_page=1")
CACHE_FINAL_HOUR = 18   # 당일 캐시는 이 시각(KST) 이후 커밋이어야 장 마감 확정본으로 본다


def _now_kst() -> pd.Timestamp:
    return pd.Timestamp.now(tz=KST).tz_localize(None)


def normalize(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """marcap / 캐시 CSV → prices_daily 형식."""
    d = df.copy()
    if "ChagesRatio" in d.columns:            # 캐시 CSV의 오타 컬럼명
        d = d.rename(columns={"ChagesRatio": "ChangesRatio"})
    d["Code"] = d["Code"].astype(str).str.zfill(6)
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
    return normalize(pd.read_parquet(path), "marcap")


def load_cache_day(day: pd.Timestamp) -> pd.DataFrame | None:
    """그날 캐시 파일이 없으면(주말·휴장·미생성) None."""
    r = requests.get(CACHE_URL.format(d=day.strftime("%Y-%m-%d")), timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = pd.read_csv(io.StringIO(r.text), index_col=0, dtype={"Code": str})
    for c in ("Close", "Changes", "Open", "High", "Low", "Volume", "Amount", "Marcap", "Stocks"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    if d["Volume"].fillna(0).sum() == 0:      # 휴장일 파일: 시세가 '-'로만 채워져 있음
        return None
    d = d[d["Close"] > 0]
    d["Date"] = day
    return normalize(d, "krx_cache")


def cache_is_final(day: pd.Timestamp) -> bool:
    """캐시 파일이 장 마감 확정본인지. 지난 날짜는 이후 갱신이 이미 있었으므로 확정으로 본다.
    당일 파일은 장중(13~14시)에도 만들어지므로, 그 파일의 마지막 커밋이 18시(KST) 이후인지 확인한다.
    확인할 수 없으면(네트워크 오류 등) 확정이 아닌 것으로 본다."""
    now = _now_kst()
    if day.normalize() < now.normalize():
        return True
    try:
        import os
        headers = {"Accept": "application/vnd.github+json"}
        if os.environ.get("GH_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GH_TOKEN']}"
        r = requests.get(CACHE_COMMITS_API.format(d=day.strftime("%Y-%m-%d")), headers=headers, timeout=30)
        r.raise_for_status()
        commits = r.json()
        if not commits:
            return False
        ts = pd.Timestamp(commits[0]["commit"]["committer"]["date"]).tz_convert(KST).tz_localize(None)
        return ts.normalize() > day.normalize() or ts.hour >= CACHE_FINAL_HOUR
    except Exception as e:  # noqa: BLE001
        print(f"  캐시 확정 여부 확인 실패 ({e}) → {day.date()} 보류")
        return False


def cache_days_after(last: pd.Timestamp | None, cutoff_hour: int = CACHE_FINAL_HOUR) -> pd.DataFrame:
    """last 다음 날부터 오늘까지 KRX 캐시 파일을 모은다. 오늘 파일은 장 마감 확정본일 때만."""
    now = _now_kst()
    end = now.normalize() if now.hour >= cutoff_hour else now.normalize() - pd.Timedelta(days=1)
    start = (pd.Timestamp(last) + pd.Timedelta(days=1)) if last is not None else end - pd.Timedelta(days=10)
    frames = []
    for day in pd.date_range(start, end, freq="D"):
        if day.weekday() >= 5:
            continue
        if not cache_is_final(day):
            print(f"  캐시 {day.date()}: 장 마감 확정본 아직 없음 → 다음 실행에서 수집")
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
  WHERE source IN ('marcap', 'krx_cache') {where}
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
