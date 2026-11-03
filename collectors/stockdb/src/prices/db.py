"""시세 테이블 스키마와 DuckDB 저장 유틸.

prices_daily 는 '원주가 + 수정계수' 방식으로 저장한다.
    수정주가 = 원주가 × adj_factor
  - 캔들·이동평균·수익률: 수정주가 (v_prices_adj 뷰)
  - 거래대금·시총: 원주가 기준 그대로
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from ..universe.config import DATA_DIR

DB_PATH = DATA_DIR / "market.duckdb"

PRICE_COLS = ["symbol_id", "date", "open", "high", "low", "close", "ref_price",
              "adj_factor", "volume", "value", "value_is_est", "mcap", "shares",
              "source", "fetched_at"]

DDL = """
CREATE TABLE IF NOT EXISTS prices_daily (
  symbol_id    TEXT,
  date         DATE,
  open         DOUBLE,     -- 원주가
  high         DOUBLE,
  low          DOUBLE,
  close        DOUBLE,
  ref_price    DOUBLE,     -- 한국: KRX 기준가(= 종가 - 전일대비). 수정계수 계산용
  adj_factor   DOUBLE,     -- 수정주가 = 원주가 × adj_factor (최신일 = 1)
  volume       DOUBLE,
  value        DOUBLE,     -- 거래대금 (통화 단위 그대로: 원, 달러)
  value_is_est BOOLEAN,    -- TRUE = 종가×거래량 추정치 (미국)
  mcap         DOUBLE,     -- 시가총액 (원). 한국 종목만
  shares       DOUBLE,     -- 상장주식수. 한국 종목만
  source       TEXT,       -- marcap / krx_cache / fdr / yf
  fetched_at   TIMESTAMP,
  PRIMARY KEY (symbol_id, date)
);
CREATE TABLE IF NOT EXISTS collect_log (
  run_at     TIMESTAMP,
  job        TEXT,
  status     TEXT,         -- ok / partial / fail
  n_symbols  INTEGER,
  n_rows     INTEGER,
  min_date   DATE,
  max_date   DATE,
  note       TEXT
);
CREATE OR REPLACE VIEW v_prices_adj AS
SELECT symbol_id, date,
       open  * adj_factor AS open,
       high  * adj_factor AS high,
       low   * adj_factor AS low,
       close * adj_factor AS close,
       close AS close_raw, adj_factor, volume, value, value_is_est, mcap, source
FROM prices_daily;
"""


def connect(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute("SET enable_progress_bar = false")
    con.execute(DDL)
    return con


def upsert_prices(con, df: pd.DataFrame) -> int:
    """같은 (symbol_id, date)는 새 값으로 덮어쓴다."""
    if df is None or df.empty:
        return 0
    df = df.copy()
    for c in PRICE_COLS:
        if c not in df.columns:
            df[c] = None
    df["fetched_at"] = df["fetched_at"].fillna(pd.Timestamp(datetime.now()))
    df = df[PRICE_COLS].drop_duplicates(["symbol_id", "date"], keep="last")
    con.register("_p", df)
    con.execute(f"INSERT OR REPLACE INTO prices_daily ({', '.join(PRICE_COLS)}) "
                f"SELECT {', '.join(PRICE_COLS)} FROM _p")
    con.unregister("_p")
    return len(df)


def log_run(con, job: str, status: str, df: pd.DataFrame | None, note: str = ""):
    n_sym = int(df["symbol_id"].nunique()) if df is not None and len(df) else 0
    n_row = int(len(df)) if df is not None else 0
    lo = df["date"].min() if n_row else None
    hi = df["date"].max() if n_row else None
    con.execute("INSERT INTO collect_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [datetime.now(), job, status, n_sym, n_row, lo, hi, note])
