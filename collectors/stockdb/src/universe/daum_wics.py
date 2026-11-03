"""다음 금융(FnGuide WICS) 업종 구성종목 → naver_upjong_members.csv 형식으로 변환.

네이버 금융 업종은 FnGuide WICS 분류를 그대로 쓰므로, 네이버에 접속할 수 없는
환경에서는 다음 금융의 WICS 데이터로 같은 결과를 만든다.

입력 텍스트 형식 (브라우저에서 다음 금융 API로 받아 저장한 파일)
    G203020|항공사|7          ← 업종코드|업종명|종목수  (업종 수만큼)
    ---
    G203020:003490,180640,... ← 업종코드:종목코드 목록   (시총 내림차순)

다음 금융 API (브라우저에서 m.finance.daum.net 접속 상태로 호출)
    업종 목록   /api/sector/wics?perPage=200&page=1
    구성종목    /api/home/sec?path=/api/sector/wics/{code}/stocks?fieldName=marketCap
                &order=desc&perPage=100&page=1&pagination=true

실행:
    python -m src.universe.daum_wics data/universe/wics_raw_20261102.txt
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from .naver_upjong import OUT_CSV


def parse_wics_text(text: str, fetched_at: str = "") -> pd.DataFrame:
    head, body = text.strip().split("\n---\n")
    meta = {}
    for line in head.splitlines():
        code, name, n = line.split("|")
        meta[code] = (name, int(n))

    rows, mismatch = [], []
    for line in body.splitlines():
        code, codes = line.split(":", 1)
        members = [c for c in codes.split(",") if c]
        name, n = meta[code]
        if len(members) != n:
            mismatch.append(f"{name}: 기대 {n}, 실제 {len(members)}")
        rows += [{"upjong_no": code, "upjong": name, "code": c} for c in members]
    if mismatch:
        raise ValueError("업종별 종목 수가 맞지 않습니다: " + "; ".join(mismatch))

    df = pd.DataFrame(rows)
    df["name"] = ""  # 종목명은 build 단계에서 KRX 상장목록으로 채움
    df["fetched_at"] = fetched_at
    df["source"] = "daum_wics"
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", type=Path)
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    a = ap.parse_args()

    m = re.search(r"(\d{8})", a.raw.name)
    fetched = pd.to_datetime(m.group(1)).date().isoformat() if m else ""
    df = parse_wics_text(a.raw.read_text(encoding="utf-8"), fetched)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out, index=False, encoding="utf-8-sig")
    print(f"저장: {a.out}  (업종 {df['upjong'].nunique()}개, 종목 {len(df)}개, 기준일 {fetched})")


if __name__ == "__main__":
    main()
