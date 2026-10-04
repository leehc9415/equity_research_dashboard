from types import SimpleNamespace

import pandas as pd

from investment_data import monthly_update


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def fetch(self, endpoint, params):
        self.calls.append((endpoint.slug, params.copy()))
        country = params.get("cntyCd", "")
        return f"{endpoint.slug}|{country}".encode()


def test_integrated_update_fetches_each_country_and_writes_no_raw(tmp_path, monkeypatch) -> None:
    frames = {
        "trade-summary": pd.DataFrame([{"date": "2026-01"}]),
        "item-trade": pd.DataFrame([{"date": "2026-01"}]),
        "country-trade": pd.DataFrame(
            [
                {"date": "2026-01", "country_code": "US"},
                {"date": "2026-01", "country_code": "CN"},
            ]
        ),
    }

    def fake_parse(payload, endpoint):
        text = payload.decode()
        if endpoint.slug == "item-country-trade":
            country = text.split("|", 1)[1]
            return SimpleNamespace(
                frame=pd.DataFrame([{"date": "2026-01", "country_code": country}])
            )
        return SimpleNamespace(frame=frames[endpoint.slug])

    monkeypatch.setattr(monthly_update, "parse_xml", fake_parse)
    monkeypatch.setattr(
        monthly_update, "update_trade_summary_timeseries", lambda *args, **kwargs: {"monthly_rows": 1}
    )
    monkeypatch.setattr(
        monthly_update, "update_item_timeseries", lambda *args, **kwargs: {"standard_monthly_hs10_rows": 1}
    )
    monkeypatch.setattr(
        monthly_update, "update_country_timeseries", lambda *args, **kwargs: {"monthly_rows": 2}
    )
    monkeypatch.setattr(
        monthly_update, "update_item_country_timeseries", lambda *args, **kwargs: {"rows": 2}
    )
    client = FakeClient()
    data_root = tmp_path / "data"

    result = monthly_update.update_all_export_timeseries(
        client,
        start_month="2026-01",
        end_month="2026-01",
        data_root=data_root,
    )

    assert [call[0] for call in client.calls[:3]] == [
        "trade-summary", "item-trade", "country-trade"
    ]
    country_calls = [call for call in client.calls if call[0] == "item-country-trade"]
    assert [call[1]["cntyCd"] for call in country_calls] == ["CN", "US"]
    assert result["raw_files_created"] == 0
    assert (data_root / "local" / "analytics" / "last-update.json").exists()
    assert not (data_root / "raw").exists()
