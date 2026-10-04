"""Editable, ordered HS6-to-industry rules for provisional sector aggregates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "dashboard" / "industry-rules.json"


class IndustryRuleError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise IndustryRuleError(f"산업 규칙 JSON에 중복 필드가 있습니다: {key}.")
        result[key] = value
    return result


def validate_rules(document: object) -> dict:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise IndustryRuleError("산업 규칙의 schemaVersion은 1이어야 합니다.")
    names = document.get("industryNames")
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n.strip() for n in names) or len(set(names)) != len(names):
        raise IndustryRuleError("industryNames가 비었거나 중복되었습니다.")
    if document.get("default") not in names:
        raise IndustryRuleError("default 산업이 industryNames에 없습니다.")
    rules = document.get("rules")
    if not isinstance(rules, list) or not rules:
        raise IndustryRuleError("rules는 비어 있지 않은 배열이어야 합니다.")
    for number, rule in enumerate(rules, 1):
        if not isinstance(rule, dict) or rule.get("industry") not in names:
            raise IndustryRuleError(f"{number}번 규칙의 산업명이 잘못되었습니다.")
        if set(rule) - {"industry", "prefixes", "chapters", "chapterRanges"}:
            raise IndustryRuleError(f"{number}번 규칙에 알 수 없는 필드가 있습니다.")
        conditions = 0
        for field in ("prefixes", "chapters", "chapterRanges"):
            if not isinstance(rule.get(field, []), list):
                raise IndustryRuleError(f"{number}번 규칙의 {field}는 배열이어야 합니다.")
        for prefix in rule.get("prefixes", []):
            if not isinstance(prefix, str) or not re.fullmatch(r"[0-9]{2,6}", prefix):
                raise IndustryRuleError(f"{number}번 규칙의 HS 접두어가 잘못되었습니다.")
            conditions += 1
        for chapter in rule.get("chapters", []):
            if type(chapter) is not int or not 1 <= chapter <= 97:
                raise IndustryRuleError(f"{number}번 규칙의 HS 장이 잘못되었습니다.")
            conditions += 1
        for bounds in rule.get("chapterRanges", []):
            if not isinstance(bounds, list) or len(bounds) != 2 or any(type(x) is not int for x in bounds) or not 1 <= bounds[0] <= bounds[1] <= 97:
                raise IndustryRuleError(f"{number}번 규칙의 HS 장 범위가 잘못되었습니다.")
            conditions += 1
        if not conditions:
            raise IndustryRuleError(f"{number}번 규칙에 적용 조건이 없습니다.")
    overrides = document.get("overrides", {})
    if not isinstance(overrides, dict):
        raise IndustryRuleError("overrides는 HS6→산업 객체여야 합니다.")
    for code, industry in overrides.items():
        if not re.fullmatch(r"[0-9]{6}", code) or industry not in names:
            raise IndustryRuleError(f"잘못된 HS6 개별 배정: {code}.")
    if not isinstance(document.get("method"), str) or not document["method"]:
        raise IndustryRuleError("method 설명이 필요합니다.")
    return document


def load_rules(path: Path = RULES_PATH) -> dict:
    try:
        return validate_rules(json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object))
    except json.JSONDecodeError as exc:
        raise IndustryRuleError(f"산업 규칙 JSON 구문 오류: {exc}") from exc


def digest(document: dict) -> str:
    canonical = json.dumps(validate_rules(document), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def industry_for_code(code: str, document: dict) -> str:
    if not re.fullmatch(r"[0-9]{6}", code):
        raise IndustryRuleError(f"잘못된 HS6 코드: {code}.")
    if code in document.get("overrides", {}):
        return document["overrides"][code]
    chapter = int(code[:2])
    for rule in document["rules"]:
        if any(code.startswith(prefix) for prefix in rule.get("prefixes", [])) or chapter in rule.get("chapters", []) or any(low <= chapter <= high for low, high in rule.get("chapterRanges", [])):
            return rule["industry"]
    return document["default"]


def expand(document: dict, codes: set[str]) -> dict[str, str]:
    validate_rules(document)
    missing = set(document.get("overrides", {})) - codes
    if missing:
        raise IndustryRuleError("원본 HS6에 없는 개별 배정: " + ", ".join(sorted(missing)[:20]))
    return {code: industry_for_code(code, document) for code in sorted(codes)}


def item_industries(catalog: list[dict], hs6_map: dict[str, str]) -> dict[str, str | None]:
    by_hs4: dict[str, set[str]] = {}
    for code, industry in hs6_map.items():
        by_hs4.setdefault(code[:4], set()).add(industry)
    result = {}
    for item in catalog:
        if not item["enabled"]:
            continue
        key = f"{item['l']}-{item['c']}"
        if item["l"] == "HS4":
            options = by_hs4.get(item["c"], set())
            result[key] = next(iter(options)) if len(options) == 1 else None
        else:
            result[key] = hs6_map.get(item["c"][:6])
    return result
