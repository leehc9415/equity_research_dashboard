"""KRX 상장종목 스냅샷 (종목코드, 종목명, 시장, 시총, 상장주식수).

1차: FinanceDataReader  StockListing('KRX')
2차: FDR GitHub 캐시     (KRX 접속이 막혀도 동작)
3차: pykrx              get_market_cap  (KRX 로그인 환경변수 KRX_ID/KRX_PW 필요할 수 있음)
결과는 data/universe/krx_listing.csv 로도 저장해 재현 가능하게 둔다.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from .config import DATA_DIR

OUT_CSV = DATA_DIR / "universe" / "krx_listing.csv"
COLS = ["code", "name", "market", "mcap_100m", "shares", "asof"]


def _from_fdr() -> pd.DataFrame:
    import FinanceDataReader as fdr

    df = fdr.StockListing("KRX")
    out = pd.DataFrame({
        "code": df["Code"].astype(str).str.zfill(6),
        "name": df["Name"],
        "market": df["Market"].str.replace(" GLOBAL", "", regex=False),
        "mcap_100m": df["Marcap"] / 1e8,
        "shares": df["Stocks"],
    })
    out["asof"] = datetime.now().date().isoformat()
    return out


CACHE_URL = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
             "refs/heads/master/data/listing/krx/{d}.csv")


def _from_github_cache(max_days_back: int = 10) -> pd.DataFrame:
    """FinanceData가 GitHub에 올려두는 일별 KRX 상장종목 캐시를 직접 읽는다.

    FDR StockListing은 '최근 거래일'을 KRX 사이트에 물어본 뒤 이 캐시를 읽는데,
    KRX 접속이 막힌 환경(해외 서버 등)에서는 그 첫 단계에서 실패한다.
    여기서는 오늘부터 거꾸로 날짜를 넣어 보며 존재하는 가장 최근 파일을 쓴다.
    """
    today = pd.Timestamp.now(tz="Asia/Seoul").normalize().tz_localize(None)
    last_err = None
    for i in range(max_days_back + 1):
        d = (today - pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            df = pd.read_csv(CACHE_URL.format(d=d), index_col=0, dtype={"Code": str})
        except Exception as e:  # noqa: BLE001 - 파일 없는 날(주말·휴장)은 건너뜀
            last_err = e
            continue
        out = pd.DataFrame({
            "code": df["Code"].str.zfill(6),
            "name": df["Name"],
            "market": df["Market"].str.replace(" GLOBAL", "", regex=False),
            "mcap_100m": df["Marcap"] / 1e8,
            "shares": df["Stocks"],
        })
        out["asof"] = d
        return out
    raise RuntimeError(f"최근 {max_days_back}일 안에 캐시 파일이 없습니다: {last_err}")


def _from_pykrx() -> pd.DataFrame:
    from pykrx import stock

    day = stock.get_nearest_business_day_in_a_week()
    frames = []
    for mkt in ("KOSPI", "KOSDAQ", "KONEX"):
        cap = stock.get_market_cap(day, market=mkt)
        if cap.empty:
            continue
        cap = cap.reset_index().rename(columns={"티커": "code", "시가총액": "mcap",
                                                "상장주식수": "shares"})
        cap["market"] = mkt
        cap["name"] = cap["code"].map(stock.get_market_ticker_name)
        frames.append(cap)
    if not frames:
        raise RuntimeError("pykrx에서 시총 데이터를 받지 못했습니다 (KRX 로그인 확인).")
    df = pd.concat(frames)
    out = pd.DataFrame({"code": df["code"], "name": df["name"], "market": df["market"],
                        "mcap_100m": df["mcap"] / 1e8, "shares": df["shares"]})
    out["asof"] = pd.Timestamp(day).date().isoformat()
    return out


def load_listing(csv_path=None, save: bool = True) -> pd.DataFrame:
    """csv_path가 주어지면 그 파일을, 아니면 FDR → pykrx 순서로 받아온다."""
    if csv_path:
        df = pd.read_csv(csv_path, dtype={"code": str})
        missing = set(COLS) - set(df.columns) - {"asof"}
        if missing:
            raise ValueError(f"listing CSV에 컬럼 누락: {missing}")
        return df

    errors = []
    for name, fn in (("FinanceDataReader", _from_fdr),
                     ("FDR GitHub 캐시", _from_github_cache),
                     ("pykrx", _from_pykrx)):
        try:
            df = fn()
            print(f"상장종목 스냅샷: {name} ({len(df)}종목, 기준 {df['asof'].iloc[0]})")
            if save:
                OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
            return df
        except Exception as e:  # noqa: BLE001 - 소스별 실패 사유를 모아서 보고
            errors.append(f"{name}: {e}")
    raise RuntimeError("상장종목 스냅샷을 받지 못했습니다.\n  " + "\n  ".join(errors))
