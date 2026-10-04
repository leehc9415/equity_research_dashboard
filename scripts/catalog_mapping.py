"""Validate and safely apply the editable dashboard item mapping."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "dashboard" / "catalog.json"
BACKUPS = ROOT / "dashboard" / "backups"
WIDTH = {"HS4": 4, "HS6": 6, "HS10": 10}


class MappingError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise MappingError(f"JSON 객체 안에 중복된 필드가 있습니다: {key}.")
        result[key] = value
    return result


def validate(document: object) -> list[dict]:
    if isinstance(document, dict):
        if document.get("schemaVersion") != 1:
            raise MappingError("지원하지 않는 매핑 형식입니다. schemaVersion은 1이어야 합니다.")
        document = document.get("items")
    if not isinstance(document, list) or not document:
        raise MappingError("items는 비어 있지 않은 배열이어야 합니다.")
    seen: set[tuple[str, str]] = set()
    clean: list[dict] = []
    for index, source in enumerate(document, 1):
        if not isinstance(source, dict):
            raise MappingError(f"{index}번 행: 객체가 아닙니다.")
        row = dict(source)
        for field in ("l", "c", "n", "i"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise MappingError(f"{index}번 행: {field}는 빈 문자열일 수 없습니다.")
            row[field] = row[field].strip()
        if row["l"] not in WIDTH or not row["c"].isascii() or not row["c"].isdigit() or len(row["c"]) != WIDTH[row["l"]]:
            raise MappingError(f"{index}번 행: {row['l']} 코드는 {WIDTH.get(row['l'], '허용되지 않는')}자리 숫자 문자열이어야 합니다.")
        for field in ("o", "p", "s"):
            value = row.get(field, "")
            if not isinstance(value, str):
                raise MappingError(f"{index}번 행: {field}는 문자열이어야 합니다.")
            row[field] = value.strip()
        for field, default in (("m", False), ("enabled", True)):
            value = row.get(field, default)
            if not isinstance(value, bool):
                raise MappingError(f"{index}번 행: {field}는 true/false여야 합니다.")
            row[field] = value
        identity = (row["l"], row["c"])
        if identity in seen:
            raise MappingError(f"{index}번 행: 중복 HS 코드 {row['l']}-{row['c']}.")
        seen.add(identity)
        clean.append(row)
    if not any(row["enabled"] for row in clean):
        raise MappingError("활성 품목이 하나 이상 필요합니다.")
    return clean


def load(path: Path = CATALOG) -> list[dict]:
    try:
        return validate(json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object))
    except json.JSONDecodeError as exc:
        raise MappingError(f"JSON 구문 오류: {exc}") from exc


def backup_current(target: Path = CATALOG, backup_dir: Path = BACKUPS) -> Path:
    load(target)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = backup_dir / f"catalog-{stamp}.json"
    shutil.copy2(target, backup)
    return backup


def apply(source: Path, target: Path = CATALOG, backup_dir: Path = BACKUPS) -> Path:
    rows = load(source)  # Reject invalid imports before touching the current file.
    if target.exists():
        load(target)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = backup_current(target, backup_dir) if target.exists() else backup_dir / f"catalog-{stamp}.json"
    temporary = target.with_name(f".{target.name}.{stamp}.tmp")
    try:
        temporary.write_text(json.dumps({"schemaVersion": 1, "items": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or apply an exported dashboard mapping")
    parser.add_argument("source", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Back up and replace dashboard/catalog.json")
    mode.add_argument("--backup", action="store_true", help="Back up the current mapping before editing")
    args = parser.parse_args()
    rows = load(args.source)
    print(f"검증 완료: {len(rows)}개 품목, 활성 {sum(r['enabled'] for r in rows)}개")
    if args.apply:
        backup = apply(args.source)
        print(f"매핑 적용 완료. 기존 파일 백업: {backup}")
    elif args.backup:
        print(f"매핑 백업 완료: {backup_current()}")


if __name__ == "__main__":
    main()
