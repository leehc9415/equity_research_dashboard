from pathlib import Path

import pandas as pd
import pytest

from investment_data.api_catalog import ENDPOINTS
from investment_data.parser import ApiResponseError, parse_file, parse_xml, profile_frame


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("slug", "filename"),
    [
        ("trade-summary", "trade_summary.xml"),
        ("item-trade", "item_trade.xml"),
        ("item-country-trade", "item_country_trade.xml"),
        ("country-trade", "country_trade.xml"),
    ],
)
def test_parses_each_endpoint(slug: str, filename: str) -> None:
    result = parse_file(FIXTURES / filename, ENDPOINTS[slug])
    assert len(result.frame) == 1
    assert result.total_count == 1
    assert result.result_code == "00"


def test_normalizes_types_without_losing_identifiers_or_unknown_fields() -> None:
    result = parse_file(
        FIXTURES / "item_country_trade.xml", ENDPOINTS["item-country-trade"]
    )
    row = result.frame.iloc[0]
    assert row["date"] == "2026-08"
    assert row["hs_code"] == "8542323000"
    assert row["export_value_usd"] == 1000
    assert pd.isna(row["import_weight_kg"])
    assert row["undocumentedField"] == "kept"


def test_profiles_hs_lengths_and_nulls() -> None:
    result = parse_file(FIXTURES / "item_trade.xml", ENDPOINTS["item-trade"])
    profile = profile_frame(result.frame)
    assert profile["hs_code_lengths"] == [10]
    assert profile["rows"] == 1


def test_raises_sanitized_api_error() -> None:
    xml = b"<response><header><resultCode>30</resultCode><resultMsg>bad key</resultMsg></header></response>"
    with pytest.raises(ApiResponseError, match="code=30"):
        parse_xml(xml, ENDPOINTS["trade-summary"])


def test_raises_gateway_auth_error_in_alternate_xml_shape() -> None:
    xml = b"""<OpenAPI_ServiceResponse><cmmMsgHeader>
    <errMsg>SERVICE ERROR</errMsg><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
    <returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"""
    with pytest.raises(ApiResponseError, match="code=30"):
        parse_xml(xml, ENDPOINTS["trade-summary"])
