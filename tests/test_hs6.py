import pandas as pd

from investment_data.hs6 import aggregate_hs6_year


def test_aggregate_hs6_preserves_residuals_and_reconciles_summary() -> None:
    items = pd.DataFrame(
        [
            {"date": "2025-01", "hs_code": "1234561000", "export_value_usd": 100, "export_weight_kg": 10, "import_value_usd": 50, "import_weight_kg": 5},
            {"date": "2025-01", "hs_code": "123456", "export_value_usd": 2, "export_weight_kg": 1, "import_value_usd": 0, "import_weight_kg": 0},
            {"date": "2025-01", "hs_code": "123456100", "export_value_usd": 3, "export_weight_kg": 1, "import_value_usd": 0, "import_weight_kg": 0},
            {"date": "2025-01", "hs_code": "999999", "export_value_usd": 4, "export_weight_kg": 1, "import_value_usd": 0, "import_weight_kg": 0},
            {"date": "합계", "hs_code": "-", "export_value_usd": 999, "export_weight_kg": 99, "import_value_usd": 999, "import_weight_kg": 99},
        ]
    )
    summary = pd.DataFrame(
        [{"date": "2025-01", "export_value_usd": 120, "import_value_usd": 55}]
    )
    result = aggregate_hs6_year(items, summary, known_hs10={"1234561000"})
    mapped = result[result["hs6_code"] == "123456"].iloc[0]
    unmapped = result[result["hs6_code"] == "UNMAPPED_HS"].iloc[0]
    adjustment = result[result["hs6_code"] == "UNALLOCATED_TO_HS"].iloc[0]
    assert mapped["export_value_usd"] == 105
    assert mapped["source_nonstandard_rows"] == 2
    assert unmapped["export_value_usd"] == 4
    assert adjustment["export_value_usd"] == 11
    assert adjustment["import_value_usd"] == 5
    assert result["export_value_usd"].sum() == 120
    assert result["import_value_usd"].sum() == 55
