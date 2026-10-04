import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from catalog_mapping import MappingError, apply, backup_current, load, validate  # noqa: E402


def sample():
    return [{"l": "HS10", "c": "8542321010", "n": "DRAM", "i": "반도체", "p": "삼성전자", "m": False}]


def test_current_catalog_is_valid():
    items = load()
    assert len(items) >= 195
    assert any(row["c"] == "8542321010" for row in items)


@pytest.mark.parametrize("change", [
    {"c": "854232101"},
    {"c": 8542321010},
    {"n": " "},
    {"m": "false"},
    {"l": "HS8"},
])
def test_invalid_fields_rejected(change):
    rows = sample()
    rows[0].update(change)
    with pytest.raises(MappingError):
        validate(rows)


def test_duplicate_code_rejected():
    with pytest.raises(MappingError, match="중복"):
        validate(sample() * 2)


def test_invalid_import_keeps_existing_mapping(tmp_path):
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(sample()), encoding="utf-8")
    source = tmp_path / "bad.json"
    source.write_text(json.dumps([{"l": "HS10", "c": "bad"}]), encoding="utf-8")
    before = target.read_bytes()
    with pytest.raises(MappingError):
        apply(source, target, tmp_path / "backups")
    assert target.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_apply_backs_up_and_replaces(tmp_path):
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(sample()), encoding="utf-8")
    rows = sample()
    rows[0]["n"] = "DRAM 수정"
    source = tmp_path / "edited.json"
    source.write_text(json.dumps({"schemaVersion": 1, "items": rows}), encoding="utf-8")
    backup = apply(source, target, tmp_path / "backups")
    assert load(backup)[0]["n"] == "DRAM"
    assert load(target)[0]["n"] == "DRAM 수정"


def test_backup_keeps_source_unchanged(tmp_path):
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(sample()), encoding="utf-8")
    before = target.read_bytes()
    backup = backup_current(target, tmp_path / "backups")
    assert target.read_bytes() == before
    assert backup.read_bytes() == before


def test_duplicate_json_field_rejected(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text('[{"l":"HS10","c":"8542321010","n":"DRAM","n":"다른 이름","i":"반도체"}]', encoding="utf-8")
    with pytest.raises(MappingError, match="중복된 필드"):
        load(path)
