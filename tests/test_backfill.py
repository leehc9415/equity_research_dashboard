from investment_data.backfill import month_chunks


def test_month_chunks_split_on_calendar_years() -> None:
    assert month_chunks("2010-03", "2012-02") == [
        ("2010-03", "2010-12"),
        ("2011-01", "2011-12"),
        ("2012-01", "2012-02"),
    ]


def test_month_chunks_single_partial_year() -> None:
    assert month_chunks("2026-01", "2026-08") == [("2026-01", "2026-08")]
