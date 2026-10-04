import copy
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from industry_rules import IndustryRuleError, expand, industry_for_code, item_industries, load_rules, validate_rules  # noqa: E402


def test_current_rules_preserve_provisional_classification():
    rules = load_rules()
    assert industry_for_code("850760", rules) == "배터리"
    assert industry_for_code("854232", rules) == "반도체·IT"
    assert industry_for_code("901390", rules) == "반도체·IT"
    assert industry_for_code("300215", rules) == "바이오·정밀"
    assert industry_for_code("870323", rules) == "자동차"
    assert industry_for_code("990000", rules) == "소비재·기타"


def test_specific_override_changes_one_code_only():
    rules = copy.deepcopy(load_rules())
    rules["overrides"]["854232"] = "배터리"
    result = expand(rules, {"854232", "854231"})
    assert result == {"854231": "반도체·IT", "854232": "배터리"}


def test_unknown_override_and_bad_industry_rejected():
    rules = copy.deepcopy(load_rules())
    rules["overrides"]["854232"] = "없는 산업"
    with pytest.raises(IndustryRuleError):
        validate_rules(rules)
    rules["overrides"]["854232"] = "배터리"
    with pytest.raises(IndustryRuleError, match="원본 HS6"):
        expand(rules, {"854231"})


def test_mixed_hs4_is_not_assigned_to_one_industry():
    rows = [{"l": "HS4", "c": "8542", "enabled": True}, {"l": "HS10", "c": "8542321010", "enabled": True}]
    mapped = item_industries(rows, {"854231": "반도체·IT", "854232": "배터리"})
    assert mapped["HS4-8542"] is None
    assert mapped["HS10-8542321010"] == "배터리"


def test_duplicate_json_rule_field_rejected(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
    with pytest.raises(IndustryRuleError, match="중복 필드"):
        load_rules(path)
