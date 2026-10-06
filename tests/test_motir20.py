import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from motir20 import load_official_series  # noqa: E402


def test_official_motir20_series_is_complete_and_converted_to_usd():
    data = load_official_series()
    assert data["coverageStart"] == "2025-05"
    assert data["coverageEnd"] == "2026-09"
    assert len(data["names"]) == 20
    assert len(data["rows"]) == 340
    petrochemical = next(row for row in data["rows"] if row[:2] == ["2026-08", "석유화학"])
    assert petrochemical[2] == 3_878_000_000
    assert petrochemical[3] == pytest.approx(0.127)
    assert data["quality"]["reconciliation"][0]["differenceUsd"] == 25_141_600
