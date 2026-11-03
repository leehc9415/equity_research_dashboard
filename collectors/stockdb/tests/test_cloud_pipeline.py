from datetime import date
from pathlib import Path
import json
import zipfile
import sys

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud_state import pack, restore, TABLES
from cloud_update import apply_global, inventory, validate_source, delayed_fred_release
from src.prices.db import connect, upsert_prices


def prices(con, dates=('2026-10-29', '2026-10-30'), sid='SPY', factor=1., source='yf'):
    upsert_prices(con, pd.DataFrame({'symbol_id': sid, 'date': [date.fromisoformat(x) for x in dates],
        'close': 100., 'open': 100., 'high': 100., 'low': 100.,
        'adj_factor': factor, 'source': source}))


def test_snapshot_roundtrip_excludes_derived_and_unrelated_tables(tmp_path):
    db = tmp_path / 'source.duckdb'
    with connect(db) as c:
        prices(c)
        prices(c, sid='KRSEC:example', source='sector_idx')
        for table in TABLES[1:]:
            c.execute(f'CREATE TABLE {table} AS SELECT 7 AS marker')
        c.execute('CREATE TABLE private_notes AS SELECT 42 AS not_market_data')
    archive = tmp_path / 'state.zip'
    pack(db, archive)
    target = tmp_path / 'restored.duckdb'
    restore(archive, target)
    with duckdb.connect(str(target), read_only=True) as c:
        assert c.execute('SELECT count(*) FROM prices_daily').fetchone()[0] == 2
        assert c.execute('SELECT marker FROM symbols').fetchone()[0] == 7
        assert not c.execute("SELECT 1 FROM duckdb_tables() WHERE table_name='private_notes'").fetchall()
    with pytest.raises(ValueError, match='overwrite'):
        restore(archive, target)


def test_snapshot_tampering_fails_before_creating_database(tmp_path):
    archive = tmp_path / 'bad.zip'
    manifest = {'schemaVersion': 1, 'tables': {t: {'rows': 1, 'sha256': 'wrong'} for t in TABLES}}
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        for table in TABLES:
            z.writestr(f'{table}.parquet', b'not valid')
    target = tmp_path / 'restored.duckdb'
    with pytest.raises(ValueError, match='checksum'):
        restore(archive, target)
    assert not target.exists()


def test_incomplete_global_collection_cannot_partially_replace_quotes(tmp_path):
    with connect(tmp_path / 'market.duckdb') as c:
        prices(c)
        before = inventory(c)
        frames = {'SPY': pd.DataFrame({'symbol_id': ['SPY'], 'date': [date(2026, 11, 2)],
            'close': [110.], 'adj_factor': [1.], 'source': ['yf']}), 'QQQ': pd.DataFrame()}
        with pytest.raises(ValueError, match='No valid'):
            apply_global(c, frames, before)
        assert inventory(c) == before


def test_global_provider_cannot_move_latest_date_backwards(tmp_path):
    with connect(tmp_path / 'market.duckdb') as c:
        prices(c)
        stale = pd.DataFrame({'symbol_id': ['SPY'], 'date': [date(2026, 10, 28)],
            'close': [90.], 'adj_factor': [1.], 'source': ['yf']})
        with pytest.raises(ValueError, match='backwards'):
            apply_global(c, {'SPY': stale}, inventory(c))


def test_distribution_adjustment_reaches_older_history_and_is_idempotent(tmp_path):
    with connect(tmp_path / 'market.duckdb') as c:
        prices(c)
        latest = pd.DataFrame({'symbol_id': ['SPY', 'SPY'],
            'date': [date(2026, 10, 30), date(2026, 11, 2)],
            'close': [100., 101.], 'adj_factor': [.9, 1.], 'source': ['yf', 'yf']})
        for _ in range(2):
            apply_global(c, {'SPY': latest}, inventory(c))
            values = c.execute('SELECT adj_factor FROM prices_daily ORDER BY date').fetchall()
            assert [x[0] for x in values] == pytest.approx([.9, .9, 1.])


def test_shrinking_history_is_rejected(tmp_path):
    with connect(tmp_path / 'market.duckdb') as c:
        prices(c)
        before = inventory(c)
        c.execute("DELETE FROM prices_daily WHERE date='2026-10-29'")
        with pytest.raises(ValueError, match='shrank'):
            validate_source(c, before)


def test_only_recent_treasury_fred_release_lag_can_retain_newer_history():
    frame = pd.DataFrame({'date': [date(2026, 10, 30)], 'source': ['fred']})
    baseline = {'UST2Y': (100, date(2026, 11, 2)), 'SPY': (100, date(2026, 11, 2))}
    assert delayed_fred_release('UST2Y', frame, baseline, date(2026, 11, 2))
    assert not delayed_fred_release('SPY', frame, baseline, date(2026, 11, 2))
    assert not delayed_fred_release('UST2Y', frame, baseline, date(2026, 11, 10))
    frame['source'] = 'yf'
    assert not delayed_fred_release('UST2Y', frame, baseline, date(2026, 11, 2))
