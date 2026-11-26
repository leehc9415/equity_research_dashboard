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
  KRX 정규장 종가는 15:30에 확정되고, 거래량·거래대금은 시간외 단일가(16:00~18:00)까지 포함해야
  KRX 공식 일봉과 같다. 그래서 당일분은 18시 이후 다음 KRX 일봉 API(다음 금융 일별 시세)로 받는다
  (2026-11-13 검증: 코스피·코스닥 2,770종목 종가 99.9%·거래량·거래대금 100% KRX 캐시와 일치,
  코넥스는 미제공). 이렇게 받은 당일분(source='krx_daum')은 다음 실행 때 KRX 캐시 확정본으로 덮어쓴다.
  KRX 캐시(FinanceData 깃허브)는 제3자 수집이라 당일 확정본 커밋이 21~23시에 올라오고,
  장중(13~14시)에도 한 번 갱신되므로 당일 파일은 18시 이후 커밋일 때만 쓴다.
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
DAUM_DAYS_URL = "https://finance.daum.net/api/quote/A{c}/days?symbolCode=A{c}&page=1&perPage=2&pagination=true"
DAUM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.daum.net/"}
DAUM_READY = (18, 5)    # 시간외 단일가(~18:00)까지 끝난 뒤에 받아야 거래량·거래대금이 KRX 일봉과 같다
KRX_SOURCES = ("marcap", "krx_cache", "krx_daum")


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


def daum_ready(now: pd.Timestamp | None = None) -> bool:
    now = now or _now_kst()
    return (now.hour, now.minute) >= DAUM_READY


def load_daum_day(codes: list[str], day: pd.Timestamp, workers: int = 8) -> pd.DataFrame:
    """당일 KRX 정규장 일봉을 종목별로 받는다(코스피·코스닥, 코넥스는 미제공).
    tradePrice=정규장 종가, totalAccTradeVolume/Price=시간외 포함 KRX 일 거래량/대금."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    target = day.strftime("%Y-%m-%d")
    session = requests.Session()

    def one(code):
        for attempt in range(3):
            try:
                r = session.get(DAUM_DAYS_URL.format(c=code), headers=DAUM_HEADERS, timeout=15)
                if r.status_code == 400:          # 미제공 종목(코넥스 등)
                    return None
                r.raise_for_status()
                for x in r.json().get("data", []):
                    if x["date"][:10] == target:
                        return x
                return None
            except Exception:  # noqa: BLE001
                time.sleep(1 + attempt)
        return None

    with ThreadPoolExecutor(workers) as ex:
        got = list(ex.map(one, codes))
    rows = []
    for code, x in zip(codes, got):
        if not x or not x.get("tradePrice"):
            continue
        close = float(x["tradePrice"])
        shares = float(x.get("listedSharesCount") or 0) or None
        rows.append({
            "symbol_id": code, "date": day.date(),
            "open": float(x.get("openingPrice") or close), "high": float(x.get("highPrice") or close),
            "low": float(x.get("lowPrice") or close), "close": close,
            "ref_price": close - float(x.get("changePrice") or 0),   # changePrice 는 부호 포함(하락 시 음수)
            "volume": float(x.get("totalAccTradeVolume") or x.get("accTradeVolume") or 0),
            "value": float(x.get("totalAccTradePrice") or x.get("accTradePrice") or 0),
            "value_is_est": False,
            "mcap": close * shares if shares else None, "shares": shares,
            "source": "krx_daum",
        })
    out = pd.DataFrame(rows)
    if len(out):
        out["fetched_at"] = pd.Timestamp(datetime.now())
    print(f"  KRX 당일분(다음 일봉) {target}: {len(out)}/{len(codes)}종목")
    return out


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
  WHERE source IN ('marcap', 'naver', 'naver_fix', 'krx_cache', 'krx_daum', 'kiwoom') {where}
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
