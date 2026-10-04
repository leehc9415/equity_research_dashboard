from pathlib import Path

import pytest

from investment_data.api_catalog import ENDPOINTS
from investment_data.client import ConfigurationError, KcsClient, collect_endpoint


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    status_code = 200
    content = (FIXTURES / "item_country_trade.xml").read_bytes()


class FakeSession:
    def __init__(self) -> None:
        self.headers = {}
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        return FakeResponse()


def test_collects_cartesian_sample_and_saves_raw_and_csv(tmp_path: Path) -> None:
    session = FakeSession()
    client = KcsClient("encoded%2Bkey", session=session)
    result = collect_endpoint(
        client,
        ENDPOINTS["item-country-trade"],
        start_month="2026-08",
        end_month="2026-08",
        hs_codes=["8542323000", "8542311000"],
        country_codes=["us", "CN"],
        output_root=tmp_path,
    )
    assert result.rows == 4
    assert len(result.raw_paths) == 4
    assert result.csv_path.exists()
    assert session.calls[0][1]["serviceKey"] == "encoded+key"
    assert {call[1]["cntyCd"] for call in session.calls} == {"US", "CN"}


def test_requires_endpoint_dimensions(tmp_path: Path) -> None:
    client = KcsClient("key", session=FakeSession())
    with pytest.raises(ConfigurationError, match="hsSgn"):
        collect_endpoint(
            client,
            ENDPOINTS["item-trade"],
            start_month="2026-08",
            end_month="2026-08",
            hs_codes=[],
            country_codes=[],
            output_root=tmp_path,
        )

