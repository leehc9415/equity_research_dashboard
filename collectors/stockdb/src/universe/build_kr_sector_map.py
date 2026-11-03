"""네이버 업종 구성종목 + 섹터 매핑(YAML) + KRX 시총 → 종목별 섹터 맵.

입력
  data/universe/naver_upjong_members.csv   (naver_upjong.py 결과)
  config/universe/kr_sectors.yaml          (네이버 업종 → 자체 섹터)
  KRX 상장종목 스냅샷                        (kr_listing.py, FDR/pykrx)
출력
  data/universe/kr_sector_map.csv          (편입·제외 사유 포함 전체 목록)
  data/universe/kr_sector_report.md        (섹터별 종목 수 vs 원형, 미매핑 업종 등)

실행:
    python -m src.universe.build_kr_sector_map
    python -m src.universe.build_kr_sector_map --listing data/universe/krx_listing.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import DATA_DIR, Universe, load_universe, norm_upjong
from .kr_listing import load_listing
from .naver_upjong import OUT_CSV as MEMBERS_CSV

OUT_MAP = DATA_DIR / "universe" / "kr_sector_map.csv"
OUT_REPORT = DATA_DIR / "universe" / "kr_sector_report.md"


def build(uni: Universe, members: pd.DataFrame, listing: pd.DataFrame
          ) -> tuple[pd.DataFrame, str]:
    rules = uni.kr["index_rules"]
    up2sec = uni.sector_by_upjong()
    sec_name = uni.sector_names()
    ignored = uni.ignored_upjong()

    m = members.copy()
    if "end_type" in m.columns:          # 네이버 API: ETF·ETN 등은 제외하고 주식만
        m = m[m["end_type"].fillna("stock") == "stock"]
    m["code"] = m["code"].astype(str).str.zfill(6)
    m = m.drop_duplicates("code", keep="first")
    m["upjong_norm"] = m["upjong"].map(norm_upjong)
    m["sector_id"] = m["upjong_norm"].map(up2sec)
    m["source"] = "naver_upjong"

    # 종목 단위 예외 적용
    for o in uni.kr.get("overrides") or []:
        code = str(o["code"]).zfill(6)
        if code in set(m["code"]):
            m.loc[m["code"] == code, ["sector_id", "source"]] = [o.get("sector"), "override"]
        else:
            m = pd.concat([m, pd.DataFrame([{"code": code, "sector_id": o.get("sector"),
                                             "source": "override"}])], ignore_index=True)

    lst = listing.copy()
    lst["code"] = lst["code"].astype(str).str.zfill(6)
    df = m.merge(lst[["code", "name", "market", "mcap_100m"]], on="code", how="left",
                 suffixes=("_naver", ""))
    df["name"] = df["name"].fillna(df.get("name_naver"))
    df["sector_name"] = df["sector_id"].map(sec_name)

    # 제외 사유 판정 (먼저 걸린 사유 하나만 기록)
    ex = rules.get("exclude", {})
    min_cap = rules["min_mcap_krw_100m"]
    reason = pd.Series("", index=df.index)

    def _flag(cond, why):
        nonlocal reason
        reason = reason.mask((reason == "") & cond, why)

    _flag(df["sector_id"].isna() & df["upjong_norm"].isin(ignored), "무시 업종")
    _flag(df["sector_id"].isna() & (df["source"] == "override"), "override 제외")
    _flag(df["sector_id"].isna(), "미매핑 업종")
    _flag(df["market"].isna(), "상장목록에 없음")
    if ex.get("preferred_shares"):
        _flag(df["code"].str[-1] != "0", "우선주")
    if ex.get("spac"):
        _flag(df["name"].fillna("").str.contains("스팩"), "스팩")
    _flag(df["market"].isin(ex.get("markets", [])), "제외 시장")
    _flag(df["mcap_100m"].fillna(0) < min_cap, f"시총 {min_cap}억 미만")
    df["exclude_reason"] = reason
    df["included"] = reason == ""

    cols = ["code", "name", "market", "mcap_100m", "upjong_no", "upjong", "sector_id",
            "sector_name", "source", "included", "exclude_reason"]
    df = df.reindex(columns=cols).sort_values(["sector_id", "mcap_100m"],
                                              ascending=[True, False])
    return df, _report(uni, df, members)


def _report(uni: Universe, df: pd.DataFrame, members: pd.DataFrame) -> str:
    inc = df[df["included"]]
    lines = ["# 한국 섹터 맵 빌드 리포트", ""]
    asof = members["fetched_at"].iloc[0] if "fetched_at" in members else "?"
    lines.append(f"- 네이버 업종 수집 시각: {asof}")
    lines.append(f"- 편입 종목: {len(inc)} / 네이버 업종 소속 전체 {len(df)}")
    lines.append("")

    lines.append("## 섹터별 종목 수 (원형 스크린샷과 비교)")
    lines.append("| 섹터 | 편입 | 원형 | 차이 | 시총(조원) | 포함 네이버 업종 |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for s in uni.kr["sectors"]:
        g = inc[inc["sector_id"] == s["id"]]
        ref = s.get("ref_count")
        diff = f"{len(g) - ref:+d}" if ref is not None else ""
        ups = g.groupby("upjong").size().sort_values(ascending=False)
        up_txt = ", ".join(f"{k} {v}" for k, v in ups.items())
        lines.append(f"| {s['name']} | {len(g)} | {ref or ''} | {diff} | "
                     f"{g['mcap_100m'].sum() / 1e4:,.1f} | {up_txt} |")
    lines.append("")

    # 설정에는 있는데 실제 네이버에 없는 업종명 (오타·업종 개편 감지)
    actual = {norm_upjong(x) for x in members["upjong"].unique()}
    missing = [(s["name"], u) for s in uni.kr["sectors"] for u in s["naver_upjong"]
               if norm_upjong(u) not in actual]
    lines.append("## 설정에 있지만 네이버에 없는 업종명")
    lines += [f"- {u}  (섹터: {sec})" for sec, u in missing] or ["- 없음"]
    lines.append("")

    # 네이버에는 있는데 어느 섹터에도 매핑 안 된 업종
    unm = df[df["exclude_reason"] == "미매핑 업종"].groupby("upjong").size()
    lines.append("## 매핑되지 않은 네이버 업종 (kr_sectors.yaml에 추가하거나 ignore_upjong에 넣기)")
    lines += [f"- {k}: {v}종목" for k, v in unm.sort_values(ascending=False).items()] \
        or ["- 없음"]
    lines.append("")

    lines.append("## 제외 사유별 종목 수")
    for k, v in df[~df["included"]].groupby("exclude_reason").size().items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="한국 섹터 맵 빌드")
    ap.add_argument("--members", type=Path, default=MEMBERS_CSV)
    ap.add_argument("--listing", type=Path, default=None,
                    help="상장종목 CSV (없으면 FDR→pykrx로 받아옴)")
    ap.add_argument("--out", type=Path, default=OUT_MAP)
    a = ap.parse_args()

    uni = load_universe()
    if uni.errors:
        raise SystemExit("설정 오류:\n  " + "\n  ".join(uni.errors))
    if not a.members.exists():
        raise SystemExit(f"{a.members} 가 없습니다. 먼저 "
                         "`python -m src.universe.naver_upjong` 을 실행하세요.")

    members = pd.read_csv(a.members, dtype={"code": str})
    listing = load_listing(a.listing)
    df, report = build(uni, members, listing)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.out, index=False, encoding="utf-8-sig")
    OUT_REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"저장: {a.out}\n리포트: {OUT_REPORT}")


if __name__ == "__main__":
    main()
