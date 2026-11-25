import json
from pathlib import Path


class LedgerIOError(RuntimeError):
    pass


def load_entries(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return []
