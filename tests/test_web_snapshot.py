import calendar
import copy
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from catalog_mapping import validate as validate_catalog  # noqa: E402
from industry_rules import digest as industry_digest  # noqa: E402
from web_snapshot import (  # noqa: E402
    COLUMNS,
    SCHEMA_VERSION,
    SnapshotError,
    catalog_digest,
    enrich_products,
    validate_snapshot,
)
from build_dashboard_data import load_region_data  # noqa: E402


def snapshot():
    catalog = validate_catalog([{"l": "HS10", "c": "8542321010", "n": "DRAM", "i": "반도체"}])
    rules = {"schemaVersion": 1, "method": "test-hs6-exclusive", "industryNames": ["반도체·IT"], "rules": [{"industry": "반도체·IT", "prefixes": ["8542"]}], "default": "반도체·IT", "overrides": {}}
    months = ["2026-01", "2026-02", "2026-03"]
    values = [100, 120, 80]
    products, quarters = enrich_products({"HS10-8542321010": [[month, value, value // 10, 20] for month, value in zip(months, values)]})
    summary = [[month, value, None, values[index] / values[index - 1] - 1 if index else None, value / calendar.monthrange(2026, int(month[-2:]))[1], 20, value - 20] for index, (month, value) in enumerate(zip(months, values))]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "columns": COLUMNS,
        "catalog": catalog,
        "catalogDigest": catalog_digest(catalog),
        "industryRules": rules,
        "industryRulesDigest": industry_digest(rules),
        "industryByHS6": {"854232": "반도체·IT"},
        "itemIndustry": {"HS10-8542321010": "반도체·IT"},
        "industryMethod": rules["method"],
        "asOf": "2026-03",
        "status": "final",
        "summary": summary,
        "industryNames": ["반도체·IT"],
        "industries": [["2026-03", "반도체·IT", 300]],
        "subindustries": [["2026-03", "반도체·IT", "8542", 300]],
        "products": products,
        "productQuarterly": quarters,
        "countryItems": {"HS10-8542321010": [["2026-03", "CN", 80]]},
        "countryNames": {"CN": "중국"},
        "regions": {},
    }


def test_snapshot_contract_and_precomputed_metrics():
    data = snapshot()
    report = validate_snapshot(data, source_catalog=data["catalog"])
    assert report["activeItems"] == 1
    assert data["productQuarterly"]["HS10-8542321010"][0][1:5] == [300, 30, 60, 10]
    assert data["products"]["HS10-8542321010"][1][5] == pytest.approx(0.2)


@pytest.mark.parametrize("change,match", [
    (lambda d: d.update(catalogDigest="bad"), "해시"),
    (lambda d: d.update(industryRulesDigest="bad"), "해시"),
    (lambda d: d["itemIndustry"].__setitem__("HS10-8542321010", "자동차"), "배정"),
    (lambda d: d["products"]["HS10-8542321010"][1].__setitem__(5, 99), "MoM"),
    (lambda d: d["productQuarterly"]["HS10-8542321010"][0].__setitem__(1, 301), "수출액"),
    (lambda d: d["countryItems"].clear(), "키"),
    (lambda d: d["countryItems"]["HS10-8542321010"].append(["2026-03", "CN", 1]), "중복"),
    (lambda d: d["subindustries"][0].__setitem__(3, 299), "합계"),
    (lambda d: d["summary"][2].__setitem__(4, 999), "일평균"),
])
def test_snapshot_rejects_inconsistent_data(change, match):
    data = copy.deepcopy(snapshot())
    change(data)
    with pytest.raises(SnapshotError, match=match):
        validate_snapshot(data)


def test_changed_mapping_requires_rebuild():
    data = snapshot()
    revised = copy.deepcopy(data["catalog"])
    revised[0]["n"] = "DRAM 수정"
    with pytest.raises(SnapshotError, match="다시 생성"):
        validate_snapshot(data, source_catalog=revised)


def test_changed_industry_rules_require_rebuild():
    data = snapshot()
    revised = copy.deepcopy(data["industryRules"])
    revised["method"] = "revised"
    with pytest.raises(SnapshotError, match="다시 생성"):
        validate_snapshot(data, source_industry_rules=revised)


def test_region_input_must_exist_and_be_versioned(tmp_path):
    path = tmp_path / "regions.json"
    with pytest.raises(FileNotFoundError):
        load_region_data(path)
    path.write_text('{"schemaVersion":2,"regions":{"854232":[["2026-03","지역","시군구",1]]}}', encoding="utf-8")
    with pytest.raises(ValueError, match="형식"):
        load_region_data(path)
