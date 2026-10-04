"""Convert the existing regional time-series workbook into a portable input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "region-series.json"
COLUMNS = ["기준월", "조회HS6", "매핑지역", "API 시군구", "수출금액(USD)"]


def convert(source: Path, target: Path = DEFAULT_OUTPUT) -> dict[str, object]:
    frame = pd.read_excel(source, sheet_name="시계열", usecols=COLUMNS)
    if frame.empty or frame[COLUMNS].isna().any().any():
        raise ValueError("지역 시계열에 빈 필수값이 있습니다.")
    frame["date"] = pd.to_datetime(frame["기준월"], errors="coerce").dt.strftime("%Y-%m")
    frame["hs6"] = frame["조회HS6"].astype(str).str.zfill(6)
    if frame["date"].isna().any() or not frame["hs6"].str.fullmatch(r"[0-9]{6}").all():
        raise ValueError("지역 시계열의 월 또는 HS6 코드가 잘못되었습니다.")
    frame["value"] = pd.to_numeric(frame["수출금액(USD)"], errors="coerce")
    if frame["value"].isna().any() or (frame["value"] < 0).any():
        raise ValueError("지역 시계열 수출금액이 잘못되었습니다.")
    frame["mapped"] = frame["매핑지역"].astype(str).str.strip()
    frame["locality"] = frame["API 시군구"].astype(str).str.strip()
    if (frame["mapped"] == "").any() or (frame["locality"] == "").any():
        raise ValueError("지역 이름이 비어 있습니다.")
    regions = {}
    for code, group in frame.sort_values(["date", "mapped", "locality"]).groupby("hs6"):
        regions[code] = [
            [row.date, row.mapped, row.locality, int(row.value) if float(row.value).is_integer() else float(row.value)]
            for row in group.itertuples()
        ]
    payload = {"schemaVersion": 1, "sourceFile": source.name, "regions": regions}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"codes": len(regions), "rows": sum(map(len, regions.values())), "output": str(target)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import regional export time series")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(convert(args.workbook, args.output))


if __name__ == "__main__":
    main()
