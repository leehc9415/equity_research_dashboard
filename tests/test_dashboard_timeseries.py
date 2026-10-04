import json

import pandas as pd

from investment_data.dashboard_timeseries import (
    _build_entity_monthly,
    _build_entity_quarterly,
    update_country_timeseries,
    update_item_country_timeseries,
)


def test_country_monthly_and_quarterly_growth_use_exact_periods() -> None:
    frame = pd.DataFrame(
        [
            {"date": "2024-01", "country_code": "US", "country_name": "미국", "export_value_usd": 310, "import_value_usd": 100, "trade_balance_usd": 210},
            {"date": "2024-02", "country_code": "US", "country_name": "미국", "export_value_usd": 290, "import_value_usd": 100, "trade_balance_usd": 190},
            {"date": "2024-03", "country_code": "US", "country_name": "미국", "export_value_usd": 310, "import_value_usd": 100, "trade_balance_usd": 210},
            {"date": "2024-04", "country_code": "US", "country_name": "미국", "export_value_usd": 600, "import_value_usd": 200, "trade_balance_usd": 400},
            {"date": "2024-05", "country_code": "US", "country_name": "미국", "export_value_usd": 620, "import_value_usd": 200, "trade_balance_usd": 420},
            {"date": "2024-06", "country_code": "US", "country_name": "미국", "export_value_usd": 600, "import_value_usd": 200, "trade_balance_usd": 400},
            {"date": "2025-01", "country_code": "US", "country_name": "미국", "export_value_usd": 620, "import_value_usd": 200, "trade_balance_usd": 420},
            {"date": "2025-02", "country_code": "US", "country_name": "미국", "export_value_usd": 580, "import_value_usd": 200, "trade_balance_usd": 380},
            {"date": "2025-03", "country_code": "US", "country_name": "미국", "export_value_usd": 620, "import_value_usd": 200, "trade_balance_usd": 420},
        ]
    )
    monthly = _build_entity_monthly(
        frame, key_columns=["country_code"], name_columns=["country_name"]
    )
    quarterly = _build_entity_quarterly(
        monthly, key_columns=["country_code"], name_columns=["country_name"]
    )

    jan_2025 = monthly[monthly["date"] == "2025-01"].iloc[0]
    q2_2024 = quarterly[quarterly["period"] == "2024-Q2"].iloc[0]
    q1_2025 = quarterly[quarterly["period"] == "2025-Q1"].iloc[0]
    assert jan_2025["export_value_yoy_pct"] == 1
    assert q2_2024["export_value_qoq_pct"] == 1820 / 910 - 1
    assert q1_2025["export_value_yoy_pct"] == 1


def test_partial_entity_quarter_suppresses_growth() -> None:
    frame = pd.DataFrame(
        [
            {"date": "2025-04", "series_key": "KR", "export_value_usd": 100, "import_value_usd": 20, "trade_balance_usd": 80},
            {"date": "2025-05", "series_key": "KR", "export_value_usd": 100, "import_value_usd": 20, "trade_balance_usd": 80},
            {"date": "2025-06", "series_key": "KR", "export_value_usd": 100, "import_value_usd": 20, "trade_balance_usd": 80},
            {"date": "2025-07", "series_key": "KR", "export_value_usd": 100, "import_value_usd": 20, "trade_balance_usd": 80},
            {"date": "2025-08", "series_key": "KR", "export_value_usd": 100, "import_value_usd": 20, "trade_balance_usd": 80},
        ]
    )
    monthly = _build_entity_monthly(frame, key_columns=["series_key"], name_columns=[])
    quarterly = _build_entity_quarterly(monthly, key_columns=["series_key"], name_columns=[])
    partial = quarterly[quarterly["period"] == "2025-Q3"].iloc[0]
    assert partial["period_status"] == "partial"
    assert pd.isna(partial["export_value_qoq_pct"])


def test_country_update_replaces_requested_month_without_raw_files(tmp_path) -> None:
    data_root = tmp_path / "data"
    output = data_root / "local" / "analytics" / "country-timeseries"
    output.mkdir(parents=True)
    initial = pd.DataFrame(
        [
            {"date": "2025-01", "country_code": "US", "country_name": "미국", "export_value_usd": 100, "import_value_usd": 20, "trade_balance_usd": 80},
            {"date": "2025-02", "country_code": "US", "country_name": "미국", "export_value_usd": 200, "import_value_usd": 20, "trade_balance_usd": 180},
        ]
    )
    monthly = _build_entity_monthly(
        initial, key_columns=["country_code"], name_columns=["country_name"]
    )
    quarterly = _build_entity_quarterly(
        monthly, key_columns=["country_code"], name_columns=["country_name"]
    )
    monthly.to_parquet(output / "monthly.parquet", index=False)
    quarterly.to_parquet(output / "quarterly.parquet", index=False)
    (output / "manifest.json").write_text(
        json.dumps({"start_month": "2025-01", "end_month": "2025-02"}), encoding="utf-8"
    )

    correction = initial.iloc[[0]].copy()
    correction["export_value_usd"] = 150
    correction["trade_balance_usd"] = 130
    manifest = update_country_timeseries(
        data_root,
        correction,
        requested_start_month="2025-01",
        requested_end_month="2025-01",
    )

    updated = pd.read_parquet(output / "monthly.parquet")
    january = updated[updated["date"] == "2025-01"].iloc[0]
    february = updated[updated["date"] == "2025-02"].iloc[0]
    assert january["export_value_usd"] == 150
    assert february["export_value_mom_pct"] == 200 / 150 - 1
    assert manifest["last_update"]["updated_months"] == ["2025-01"]
    assert not (data_root / "raw").exists()


def test_item_country_update_replaces_partition_month(tmp_path) -> None:
    data_root = tmp_path / "data"
    output = data_root / "local" / "analytics" / "item-country-timeseries"
    partition = output / "hs10-monthly" / "year=2025" / "chapter=85"
    partition.mkdir(parents=True)
    rows = pd.DataFrame(
        [
            _item_country_row("2025-01", 100),
            _item_country_row("2025-02", 200),
        ]
    )
    from investment_data.dashboard_timeseries import _prepare_item_country_rows

    prepared, _ = _prepare_item_country_rows(rows)
    prepared.to_parquet(partition / "data.parquet", index=False)
    (output / "manifest.json").write_text(
        json.dumps({"start_month": "2025-01", "end_month": "2025-02"}), encoding="utf-8"
    )
    correction = pd.DataFrame([_item_country_row("2025-01", 175)])

    manifest = update_item_country_timeseries(
        data_root,
        correction,
        requested_start_month="2025-01",
        requested_end_month="2025-01",
    )

    updated = pd.read_parquet(partition / "data.parquet")
    assert updated.loc[updated["date"] == "2025-01", "export_value_usd"].iloc[0] == 175
    assert updated.loc[updated["date"] == "2025-02", "export_value_usd"].iloc[0] == 200
    assert manifest["last_update"]["touched_partition_files"] == 1


def _item_country_row(date: str, value: int) -> dict[str, object]:
    return {
        "date": date,
        "hs_code": "8542321010",
        "product_name": "테스트",
        "country_code": "US",
        "country_name": "미국",
        "export_value_usd": value,
        "export_weight_kg": 10,
        "import_value_usd": 20,
        "import_weight_kg": 2,
        "trade_balance_usd": value - 20,
    }
