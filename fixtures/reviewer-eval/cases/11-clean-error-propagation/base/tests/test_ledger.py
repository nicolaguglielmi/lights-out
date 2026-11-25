from pathlib import Path
from src.ledger import load_entries


def test_load_entries_missing_returns_empty(tmp_path: Path) -> None:
    assert load_entries(tmp_path / 'missing.json') == []
