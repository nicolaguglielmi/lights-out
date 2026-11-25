import json
from pathlib import Path


class LedgerIOError(RuntimeError):
    pass


def load_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerIOError(f'failed to load ledger {path}: {exc}') from exc
    if not isinstance(data, list):
        raise LedgerIOError(f'ledger {path} must hold a JSON list')
    return data
