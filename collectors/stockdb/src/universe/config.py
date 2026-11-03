"""유니버스 설정(YAML) 로더와 검증.

config/universe/ 아래 3개 파일을 읽어 한 번에 검증한다.
  - kr_sectors.yaml : 한국 업종 (네이버 업종 → 자체 섹터 매핑)
  - us_etf.yaml     : 미국 지수·섹터 ETF
  - macro.yaml      : 상품·통화·금리
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config" / "universe"
DATA_DIR = ROOT / "data"

_NORM_RE = re.compile(r"[\s,·ㆍ/()]")


def norm_upjong(name: str) -> str:
    """업종명 비교용 정규화: 공백·쉼표·가운뎃점·괄호 제거."""
    return _NORM_RE.sub("", str(name)).strip()


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class Universe:
    kr: dict
    us: dict
    macro: dict
    errors: list[str] = field(default_factory=list)

    # ---- 편의 접근자 -------------------------------------------------
    def sector_by_upjong(self) -> dict[str, str]:
        """정규화된 네이버 업종명 → 섹터 id."""
        out: dict[str, str] = {}
        for s in self.kr["sectors"]:
            for u in s["naver_upjong"]:
                out[norm_upjong(u)] = s["id"]
        return out

    def sector_names(self) -> dict[str, str]:
        return {s["id"]: s["name"] for s in self.kr["sectors"]}

    def ignored_upjong(self) -> set[str]:
        return {norm_upjong(u) for u in self.kr.get("ignore_upjong", [])}


def load_universe(config_dir: Path = CONFIG_DIR) -> Universe:
    u = Universe(
        kr=_read(config_dir / "kr_sectors.yaml"),
        us=_read(config_dir / "us_etf.yaml"),
        macro=_read(config_dir / "macro.yaml"),
    )
    u.errors = validate(u)
    return u


def validate(u: Universe) -> list[str]:
    """설정 오류를 모아 반환 (비어 있으면 정상)."""
    errs: list[str] = []

    # 1) 섹터 id 중복, 한 업종이 두 섹터에 매핑되는지
    seen_sector: set[str] = set()
    upjong_owner: dict[str, str] = {}
    for s in u.kr["sectors"]:
        for key in ("id", "name", "naver_upjong"):
            if key not in s:
                errs.append(f"[kr] 섹터에 '{key}' 누락: {s}")
        if s.get("id") in seen_sector:
            errs.append(f"[kr] 섹터 id 중복: {s['id']}")
        seen_sector.add(s.get("id"))
        for up in s.get("naver_upjong", []):
            n = norm_upjong(up)
            if n in upjong_owner and upjong_owner[n] != s["id"]:
                errs.append(f"[kr] 네이버 업종 '{up}'이 두 섹터에 매핑됨: "
                            f"{upjong_owner[n]}, {s['id']}")
            upjong_owner[n] = s["id"]

    for up in u.kr.get("ignore_upjong", []):
        if norm_upjong(up) in upjong_owner:
            errs.append(f"[kr] '{up}'이 ignore_upjong과 섹터 매핑에 동시에 있음")

    for o in u.kr.get("overrides") or []:
        if o.get("sector") is not None and o["sector"] not in seen_sector:
            errs.append(f"[kr] override의 섹터 id가 없음: {o}")
        if not re.fullmatch(r"\d{6}|[0-9A-Z]{6}", str(o.get("code", ""))):
            errs.append(f"[kr] override 종목코드 형식 오류: {o}")

    # 2) 전체 심볼 id 중복 (그룹이 달라도 symbols 테이블 PK가 같으면 충돌)
    ids: dict[str, str] = {}

    def _reg(sym_id: str, where: str):
        if sym_id in ids:
            errs.append(f"심볼 id 중복: {sym_id} ({ids[sym_id]}, {where})")
        ids[sym_id] = where

    for p in u.kr.get("pinned", []):
        _reg(p["id"], "kr.pinned")
    for p in u.us.get("pinned", []) + u.us.get("symbols", []):
        _reg(p["id"], "us")
    for m in u.macro.get("symbols", []):
        _reg(m["id"], "macro")
        if "ticker" not in m:
            errs.append(f"[macro] ticker 누락: {m['id']}")

    # 3) 벤치마크가 실제로 존재하는지
    if u.us["group"]["benchmark"] not in ids:
        errs.append(f"[us] 벤치마크 {u.us['group']['benchmark']}가 심볼 목록에 없음")
    if u.kr["group"]["benchmark"] not in ids:
        errs.append(f"[kr] 벤치마크 {u.kr['group']['benchmark']}가 심볼 목록에 없음")

    return errs


if __name__ == "__main__":
    uni = load_universe()
    n_up = sum(len(s["naver_upjong"]) for s in uni.kr["sectors"])
    print(f"한국 업종: 섹터 {len(uni.kr['sectors'])}개, 네이버 업종 {n_up}개 매핑, "
          f"고정 지수 {len(uni.kr['pinned'])}개")
    print(f"미국 ETF : {len(uni.us['pinned']) + len(uni.us['symbols'])}개")
    inferred = sum(1 for m in uni.macro["symbols"] if m.get("inferred"))
    print(f"매크로   : {len(uni.macro['symbols'])}개 (그중 추정 {inferred}개)")
    if uni.errors:
        print("\n설정 오류:")
        for e in uni.errors:
            print("  -", e)
        raise SystemExit(1)
    print("\n설정 검증 통과")
