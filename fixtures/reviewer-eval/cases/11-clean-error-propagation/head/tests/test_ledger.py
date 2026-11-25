from pathlib import Path
import pytest
from src.ledger import LedgerIOError, load_entries


def test_load_entries_missing_returns_empty(tmp_path: Path) -> None:
    assert load_entries(tmp_path / 'missing.json') == []


def test_load_entries_valid_json(tmp_path: Path) -> None:
    p = tmp_path / 'ledger.json'
    p.write_text('[{"id": 1}]', encoding='utf-8')
    assert load_entries(p) == [{'id': 1}]


def test_load_entries_corrupt_json_raises(tmp_path: Path) -> None:
    p = tmp_path / 'corrupt.json'
    p.write_text('{bad json', encoding='utf-8')
    with pytest.raises(LedgerIOError):
        load_entries(p)


def test_load_entries_non_list_raises(tmp_path: Path) -> None:
    p = tmp_path / 'obj.json'
    p.write_text('{"a": 1}', encoding='utf-8')
    with pytest.raises(LedgerIOError):
        load_entries(p)
