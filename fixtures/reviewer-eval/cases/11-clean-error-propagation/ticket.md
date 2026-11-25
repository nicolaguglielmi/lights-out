# 0111 — Propagate LedgerIOError instead of returning [] on corrupt JSON in src/ledger.py

- Status: open
- Labels: bug, reliability

## Problem

`load_entries(path)` catches `Exception` and returns `[]`, hiding corrupted ledger files and I/O errors as if the ledger were empty.

## Acceptance criteria

Given a missing ledger file (`not path.exists()`)
When `load_entries(path)` runs
Then it returns `[]`.

Given an existing file containing invalid JSON, non-list JSON, or unreadable permissions
When `load_entries(path)` runs
Then it raises `LedgerIOError` (chained from the underlying exception where applicable).

Given `tests/test_ledger.py`
When tests run
Then missing file, valid list, corrupt JSON, and non-list JSON are all covered.

## Perimeter

- `src/ledger.py`
- `tests/test_ledger.py`
