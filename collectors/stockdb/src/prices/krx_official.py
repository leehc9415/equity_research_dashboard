"""KRX 정보데이터시스템 [12001] 전종목 시세로 정규장 확정 시세를 받아 덮어쓴다 (pykrx, 로그인 필요).

왜 필요한가
  네이버 polling 값은 장 마감 뒤 시간외 단일가·NXT 거래가 섞인 가격이라 정규장 종가와 다르다
  (예: BGF리테일 2026-11-09 정규장 139,200 / 네이버 16:42 146,400).
  FinanceData KRX 캐시(github)는 장중 스냅샷이 그대로 남은 날이 있어(36영업일 중 13일) 못 쓴다.
  KRX 원천은 HTS·스탁이지와 같은 정규장 시·고·저·종가, 거래량·거래대금, 시총, 기준가(종가-대비)를 준다.

로그인: 환경 변수 KRX_ID, KRX_PW (깃허브 Actions 는 저장소 Secrets 에서 넣는다).
로그인이 안 되거나 KRX 가 응답하지 않으면 경고만 남기고 네이버 값을 유지한다(수집을 멈추지 않음).
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import pandas as pd

from .db import log_run, upsert_prices
from .kr import changed_factor_symbols, recompute_adj_factor

KST = "Asia/Seoul"
FIRST_NAVER_DAY = pd.Timestamp("2026-09-14")   # 이 날부터 네이버로 들어간 구간 → KRX 로 교정 대상


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.strip(), errors="coerce")


def fetch_day(day: pd.Timestamp) -> pd.DataFrame:
    """그날 전 종목(코스피·코스닥·코넥스) 정규장 시세. 휴장일이면 빈 프레임."""
    from pykrx.website.krx.market.core import 전종목시세

    raw = 전종목시세().fetch(day.strftime("%Y%m%d"), "ALL")
    if raw is None or raw.empty or "TDD_CLSPRC" not in raw:
        return pd.DataFrame()
    close, chg = _num(raw["TDD_CLSPRC"]), _num(raw["CMPPREVDD_PRC"])
    out = pd.DataFrame({
        "symbol_id": raw["ISU_SRT_CD"].astype(str).str.zfill(6),
        "date": day.date(),
        "open": _num(raw["TDD_OPNPRC"]), "high": _num(raw["TDD_HGPRC"]), "low": _num(raw["TDD_LWPRC"]),
        "close": close, "ref_price": close - chg,
        "volume": _num(raw["ACC_TRDVOL"]), "value": _num(raw["ACC_TRDVAL"]), "value_is_est": False,
        "mcap": _num(raw["MKTCAP"]), "shares": _num(raw["LIST_SHRS"]),
        "source": "krx",
    })
    out = out[out["close"] > 0].copy()
    if out.empty or out["volume"].sum() == 0:      # 휴장일
        return pd.DataFrame()
    for c in ("open", "high", "low"):                # 거래 없는 종목은 0 → 종가로
        out[c] = out[c].where(out[c] > 0, out["close"])
    out["fetched_at"] = pd.Timestamp(datetime.now())
    return out


def available() -> bool:
    return bool(os.getenv("KRX_ID") and os.getenv("KRX_PW"))


def overlay(con, codes: list[str], start, end, sleep: float = 1.0) -> pd.DataFrame:
    """start~end 영업일을 KRX 정규장 시세로 덮어쓴다.

    오늘 값은 18:10(KST) 이후에만 받는다. 그 전에는 KRX 도 시간외 단일가(16:00~18:00) 진행 중 가격을
    종가 칸에 주기 때문(2026-11-09 17:42 확인: BGF리테일 145,400 / 정규장 139,200).
    """
    if not available():
        print("  KRX 로그인 정보(KRX_ID/KRX_PW)가 없어 정규장 교정을 건너뜀 → 네이버 값 유지")
        log_run(con, "kr_krx", "skip", pd.DataFrame(), note="no credentials")
        return pd.DataFrame()
    now = pd.Timestamp.now(tz=KST).tz_localize(None)
    keep = set(codes)
    frames, failed = [], []
    for day in pd.bdate_range(start, end):
        if day.normalize() == now.normalize() and now.hour * 60 + now.minute < 18 * 60 + 10:
            continue
        try:
            f = fetch_day(day)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{day.date()}:{type(e).__name__}")
            continue
        if not f.empty:
            frames.append(f[f["symbol_id"].isin(keep)])
        time.sleep(sleep)
    if not frames:
        print(f"  KRX 정규장 교정: 받은 날 없음 {failed[:3]}")
        log_run(con, "kr_krx", "fail", pd.DataFrame(), note=",".join(failed)[:500])
        return pd.DataFrame()
    new = pd.concat(frames, ignore_index=True)
    con.register("_n", new[["symbol_id", "date"]])
    old = con.execute("SELECT p.symbol_id, p.date, p.adj_factor FROM prices_daily p JOIN _n USING (symbol_id, date)").df()
    con.unregister("_n")
    old["date"] = pd.to_datetime(old["date"]).dt.date
    new = new.merge(old, on=["symbol_id", "date"], how="left")
    new["adj_factor"] = new["adj_factor"].fillna(1.0)
    upsert_prices(con, new)
    syms = changed_factor_symbols(con, new)          # 권리 변동(기준가 ≠ 전일 종가) 종목만 수정계수 재계산
    if syms:
        recompute_adj_factor(con, syms)
    days = sorted(new["date"].unique())
    print(f"  KRX 정규장 교정: {days[0]} ~ {days[-1]} ({len(days)}영업일, {len(new):,}행), "
          f"권리 변동 {len(syms)}종목, 실패 {failed[:3]}")
    log_run(con, "kr_krx", "ok" if not failed else "partial", new, note=",".join(failed)[:500])
    return new


def overlay_since_naver(con, codes: list[str], td) -> pd.DataFrame:
    """아직 KRX 로 교정되지 않은 날(네이버 값) 전부 + 최근 7영업일을 교정."""
    lo = pd.Timestamp(td) - pd.tseries.offsets.BDay(7)
    first = con.execute("""SELECT min(date) FROM prices_daily
        WHERE source IN ('naver', 'naver_fix', 'krx_cache') AND date >= ?""", [FIRST_NAVER_DAY.date()]).fetchone()[0]
    if first is not None:
        lo = min(lo, pd.Timestamp(first))
    return overlay(con, codes, lo, td)
