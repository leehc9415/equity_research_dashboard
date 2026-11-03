"""네이버 증권 업종 구성종목 수집기.

네이버 금융 업종 페이지(finance.naver.com/sise/sise_group.naver)는 2026년 하반기
새 사이트(stock.naver.com)로 이전되었다. 새 사이트는 아래 JSON API로 데이터를 받는다.

    업종 목록   https://m.stock.naver.com/api/stocks/industry?page=1&pageSize=100
    구성종목    https://m.stock.naver.com/api/stocks/industry/{no}?page=N&pageSize=100

결과: data/universe/naver_upjong_members.csv
      (upjong_no, upjong, code, name, sosok, mcap_100m, end_type, fetched_at, source)

실행 (국내 PC, 한 달에 한 번 정도면 충분):
    python -m src.universe.naver_upjong
    python -m src.universe.naver_upjong --limit 3      # 3개 업종만 시험

주의: 공식 API가 아니라 웹 화면이 쓰는 내부 주소다. 저빈도 개인 연구용으로만 쓰고
요청 간 대기(--sleep)를 줄이지 말 것. 응답 구조가 바뀌면 parse_* 함수만 고치면 된다.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from .config import DATA_DIR

BASE = "https://m.stock.naver.com/api/stocks/industry"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Referer": "https://m.stock.naver.com/",
}
PAGE_SIZE = 100
OUT_CSV = DATA_DIR / "universe" / "naver_upjong_members.csv"


# ---------------------------------------------------------------- 파서
def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_upjong_list(js: dict) -> list[tuple[int, str, int]]:
    """업종 목록 JSON → [(업종번호, 업종명, 종목수)]."""
    return [(int(g["no"]), g["name"].strip(), int(g.get("totalCount", 0)))
            for g in js.get("groups", [])]


def parse_upjong_detail(js: dict) -> list[dict]:
    """업종 구성종목 JSON → 종목 dict 목록.

    marketValue 단위는 억원. sosok: '0'=코스피, '1'=코스닥.
    stockEndType: stock(보통·우선주) / etf / etn 등.
    """
    out = []
    for s in js.get("stocks", []):
        out.append({
            "code": str(s.get("itemCode", "")).strip(),
            "name": str(s.get("stockName", "")).strip(),
            "sosok": {"0": "KOSPI", "1": "KOSDAQ"}.get(str(s.get("sosok")), s.get("sosok")),
            "mcap_100m": _num(s.get("marketValue")),
            "end_type": s.get("stockEndType"),
        })
    return out


# ---------------------------------------------------------------- 수집
def _get(session: requests.Session, url: str, retries: int = 3) -> dict:
    for i in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if i == retries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise RuntimeError("unreachable")


def collect(sleep: float = 0.5, limit: int | None = None) -> pd.DataFrame:
    s = requests.Session()
    upjongs = parse_upjong_list(_get(s, f"{BASE}?page=1&pageSize={PAGE_SIZE}"))
    if not upjongs:
        raise RuntimeError("업종 목록을 하나도 읽지 못했습니다. API 응답 구조를 확인하세요.")
    if limit:
        upjongs = upjongs[:limit]

    rows, mismatch = [], []
    for i, (no, upjong, total) in enumerate(upjongs, 1):
        members, page = [], 1
        while True:
            js = _get(s, f"{BASE}/{no}?page={page}&pageSize={PAGE_SIZE}")
            batch = parse_upjong_detail(js)
            members += batch
            if len(batch) < PAGE_SIZE or len(members) >= js.get("totalCount", total):
                break
            page += 1
            time.sleep(sleep)
        if len(members) != total:
            mismatch.append(f"{upjong}: 목록 {total} / 수집 {len(members)}")
        n_stock = sum(1 for m in members if m["end_type"] == "stock")
        print(f"[{i:>2}/{len(upjongs)}] {upjong:<20} no={no:<4} {len(members):>4}개 (주식 {n_stock})")
        for m in members:
            rows.append({"upjong_no": no, "upjong": upjong, **m})
        time.sleep(sleep)

    df = pd.DataFrame(rows)
    df["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    df["source"] = "naver_api"
    if mismatch:
        print("\n[경고] 종목 수 불일치: " + "; ".join(mismatch))
    dup = df[(df["end_type"] == "stock") & df.duplicated("code", keep=False)]
    if len(dup):
        print(f"\n[경고] 두 개 이상 업종에 속한 종목 {dup['code'].nunique()}개 (첫 업종만 사용)")
    return df


def main():
    ap = argparse.ArgumentParser(description="네이버 증권 업종 구성종목 수집")
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    ap.add_argument("--sleep", type=float, default=0.5, help="요청 간 대기(초)")
    ap.add_argument("--limit", type=int, default=None, help="앞에서 N개 업종만 (시험용)")
    a = ap.parse_args()

    df = collect(sleep=a.sleep, limit=a.limit)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out, index=False, encoding="utf-8-sig")
    stk = df[df["end_type"] == "stock"]
    print(f"\n저장: {a.out}  (업종 {df['upjong_no'].nunique()}개, 주식 {stk['code'].nunique()}종목, "
          f"전체 {len(df)}행)")


if __name__ == "__main__":
    main()
