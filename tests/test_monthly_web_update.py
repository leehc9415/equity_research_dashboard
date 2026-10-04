import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import monthly_web_update as update  # noqa: E402
from test_web_snapshot import snapshot  # noqa: E402
from web_snapshot import validate_snapshot  # noqa: E402


class FakeSession:
    def __init__(self, html: str) -> None:
        self.html = html

    def get(self, url, params, timeout):
        return SimpleNamespace(text=self.html, raise_for_status=lambda: None)


def test_release_gate_rejects_provisional_and_accepts_exact_final():
    provisional = '<a class="nttInfoBtn" title="2026년 8월 월간 수출입 현황 [잠정치]" data-id="1" data-url="abc">x</a>'
    final = '<a class="nttInfoBtn" title="2026년 8월 월간 수출입 현황 [확정치]" data-id="123" data-url="abc">x</a>'
    assert update.final_release_url("2026-08", session=FakeSession(provisional)) is None
    assert update.final_release_url("2026-08", session=FakeSession(final)).endswith("nttSn=123&nttSnUrl=abc")
    assert update.final_release_url("2026-09", session=FakeSession(final)) is None


def test_yearly_revision_period_is_under_twelve_month_api_limit():
    assert update._periods("2026-01") == [("2026-01", "2026-01")]
    assert update._periods("2026-02") == [("2025-01", "2025-12"), ("2026-01", "2026-02")]
    assert update._periods("2026-09") == [("2026-01", "2026-09")]


def _fake_api(slug, start, end, **extra):
    months = [f"2026-{number:02d}" for number in range(1, 5)]
    values = [100, 120, 80, 200]
    if slug == "trade-summary":
        return pd.DataFrame([{"date": month, "export_value_usd": value, "import_value_usd": 20} for month, value in zip(months, values)])
    if slug == "item-trade":
        return pd.DataFrame([{"date": month, "hs_code": "8542321010", "export_value_usd": value, "export_weight_kg": value // 10, "import_value_usd": 20} for month, value in zip(months, values)])
    if slug == "country-trade":
        return pd.DataFrame([{"date": month, "country_code": "CN", "country_name": "중국", "export_value_usd": value} for month, value in zip(months, values)])
    if slug == "item-country-trade":
        return pd.DataFrame([{"date": month, "hs_code": "8542321010", "export_value_usd": value} for month, value in zip(months, values)])
    raise AssertionError(slug)


def test_hosted_merge_updates_all_web_sections_without_parquet(monkeypatch):
    monkeypatch.setattr(update, "_api_frame", lambda client, slug, start, end, **extra: _fake_api(slug, start, end, **extra))
    result = update.merge_periods(copy.deepcopy(snapshot()), object(), "2026-04")
    assert result["asOf"] == "2026-04"
    assert result["summary"][-1][1] == 200
    assert result["industries"][-1] == ["2026-04", "반도체·IT", 200]
    assert result["products"]["HS10-8542321010"][-1][1] == 200
    assert result["countryItems"]["HS10-8542321010"][-1] == ["2026-04", "CN", 200]
    validate_snapshot(result, source_catalog=result["catalog"], source_industry_rules=result["industryRules"])


def test_incomplete_country_response_does_not_publish(monkeypatch):
    def incomplete(client, slug, start, end, **extra):
        frame = _fake_api(slug, start, end, **extra)
        if slug == "item-country-trade":
            frame = frame[frame.date != "2026-04"]
        return frame

    monkeypatch.setattr(update, "_api_frame", incomplete)
    with pytest.raises(RuntimeError, match="국가별 합계"):
        update.merge_periods(copy.deepcopy(snapshot()), object(), "2026-04")


def test_metadata_only_mapping_change_is_safe():
    data = snapshot()
    edited = copy.deepcopy(data["catalog"])
    edited[0]["n"] = "새 표시 이름"
    update._metadata_sync(data, edited, data["industryRules"])
    assert data["catalog"][0]["n"] == "새 표시 이름"
    validate_snapshot(data, source_catalog=edited, source_industry_rules=data["industryRules"])
