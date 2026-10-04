import json

import pandas as pd

from investment_data.timeseries import (
    build_monthly_timeseries,
    build_quarterly_timeseries,
    update_item_timeseries,
    upsert_monthly_timeseries,
)


def _row(date: str, code: str, value: int, weight: int, name: str = "테스트") -> dict:
    return {
        "date": date,
        "hs_code": code,
        "product_name": name,
        "export_value_usd": value,
        "export_weight_kg": weight,
        "import_value_usd": 10,
        "import_weight_kg": 2,
        "trade_balance_usd": value - 10,
    }


def test_monthly_growth_uses_exact_calendar_periods_and_calendar_days() -> None:
    items = pd.DataFrame(
        [
            _row("2024-01", "8542321010", 310, 10),
            _row("2024-02", "8542321010", 290, 10),
            _row("2025-01", "8542321010", 620, 20),
        ]
    )
    result = build_monthly_timeseries(items, level="hs10")
    jan_2025 = result[result["date"] == "2025-01"].iloc[0]
    feb_2024 = result[result["date"] == "2024-02"].iloc[0]

    assert jan_2025["avg_daily_export_usd"] == 20
    assert jan_2025["export_value_yoy_pct"] == 1
    assert pd.isna(jan_2025["export_value_mom_pct"])
    assert feb_2024["avg_daily_export_usd"] == 10
    assert feb_2024["export_value_mom_pct"] == 290 / 310 - 1


def test_hs6_and_quarterly_unit_prices_are_weighted() -> None:
    items = pd.DataFrame(
        [
            _row("2024-01", "8542321010", 100, 10),
            _row("2024-01", "8542321020", 300, 10),
            _row("2024-02", "8542321010", 200, 20),
            _row("2024-03", "8542321010", 300, 30),
            _row("2024-04", "8542321010", 400, 20),
            _row("2024-05", "8542321010", 500, 25),
            _row("2024-06", "8542321010", 600, 30),
            _row("2025-01", "8542321010", 200, 10),
            _row("2025-02", "8542321010", 400, 20),
            _row("2025-03", "8542321010", 600, 30),
        ]
    )
    monthly = build_monthly_timeseries(items, level="hs6")
    january = monthly[monthly["date"] == "2024-01"].iloc[0]
    assert january["hs_code"] == "854232"
    assert january["export_unit_price_usd_per_kg"] == 20

    available = [
        "2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06",
        "2025-01", "2025-02", "2025-03",
    ]
    quarterly = build_quarterly_timeseries(monthly, available_months=available)
    q1_2024 = quarterly[quarterly["period"] == "2024-Q1"].iloc[0]
    q2_2024 = quarterly[quarterly["period"] == "2024-Q2"].iloc[0]
    q1_2025 = quarterly[quarterly["period"] == "2025-Q1"].iloc[0]

    assert q1_2024["export_value_usd"] == 900
    assert q1_2024["export_unit_price_usd_per_kg"] == 900 / 70
    assert q2_2024["export_value_qoq_pct"] == 1500 / 900 - 1
    assert q1_2025["export_value_yoy_pct"] == 1200 / 900 - 1


def test_partial_quarter_retains_values_but_suppresses_growth() -> None:
    items = pd.DataFrame(
        [
            _row("2025-04", "8542321010", 100, 10),
            _row("2025-05", "8542321010", 100, 10),
            _row("2025-06", "8542321010", 100, 10),
            _row("2025-07", "8542321010", 100, 10),
            _row("2025-08", "8542321010", 100, 10),
        ]
    )
    monthly = build_monthly_timeseries(items, level="hs10")
    quarterly = build_quarterly_timeseries(
        monthly,
        available_months=["2025-04", "2025-05", "2025-06", "2025-07", "2025-08"],
    )
    partial = quarterly[quarterly["period"] == "2025-Q3"].iloc[0]
    assert partial["period_status"] == "partial"
    assert partial["export_value_usd"] == 200
    assert pd.isna(partial["export_value_qoq_pct"])


def test_upsert_replaces_month_and_recalculates_following_growth() -> None:
    original_items = pd.DataFrame(
        [
            _row("2025-01", "8542321010", 100, 10),
            _row("2025-02", "8542321010", 200, 20),
            _row("2026-01", "8542321010", 300, 30),
        ]
    )
    existing = build_monthly_timeseries(original_items, level="hs10")
    correction = pd.DataFrame([_row("2025-01", "8542321010", 150, 10)])

    updated = upsert_monthly_timeseries(existing, correction, level="hs10")
    jan_2025 = updated[updated["date"] == "2025-01"].iloc[0]
    feb_2025 = updated[updated["date"] == "2025-02"].iloc[0]
    jan_2026 = updated[updated["date"] == "2026-01"].iloc[0]

    assert jan_2025["export_value_usd"] == 150
    assert feb_2025["export_value_mom_pct"] == 200 / 150 - 1
    assert jan_2026["export_value_yoy_pct"] == 1


def test_direct_update_writes_only_analytics_files(tmp_path) -> None:
    data_root = tmp_path / "data"
    output = data_root / "local" / "analytics" / "item-timeseries"
    output.mkdir(parents=True)
    initial_items = pd.DataFrame(
        [
            _row("2025-01", "8542321010", 100, 10),
            _row("2025-02", "8542321010", 200, 20),
        ]
    )
    for level in ("hs10", "hs6"):
        monthly = build_monthly_timeseries(initial_items, level=level)
        quarterly = build_quarterly_timeseries(
            monthly, available_months=["2025-01", "2025-02"]
        )
        monthly.to_parquet(output / f"{level}_monthly.parquet", index=False)
        quarterly.to_parquet(output / f"{level}_quarterly.parquet", index=False)
    (output / "manifest.json").write_text(
        json.dumps({"start_month": "2025-01", "end_month": "2025-02"}),
        encoding="utf-8",
    )

    correction = pd.DataFrame([_row("2025-01", "8542321010", 150, 10)])
    manifest = update_item_timeseries(
        data_root,
        correction,
        requested_start_month="2025-01",
        requested_end_month="2025-01",
    )

    updated = pd.read_parquet(output / "hs10_monthly.parquet")
    assert updated.loc[updated["date"] == "2025-01", "export_value_usd"].iloc[0] == 150
    assert manifest["last_update"]["mode"] == "direct-timeseries-upsert"
    assert not (data_root / "raw").exists()
