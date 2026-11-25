# 0102 — Surface audit store write failures as AuditWriteError in billing audit

- Status: open
- Labels: bug, reliability

## Problem

`record_invoice_event(store, invoice_id, amount_cents)` invokes `store.append(...)` without normalizing storage errors into the domain exception `AuditWriteError`.

## Acceptance criteria

Given an audit store whose `append` method raises `OSError`
When `record_invoice_event(store, invoice_id, amount_cents)` runs
Then it raises `AuditWriteError` chained from the underlying error, never swallowing failures silently.

Given the test suite in `tests/test_billing_audit.py`
When `store.append` fails
Then a unit test asserts that `AuditWriteError` is raised.

## Perimeter

- `src/billing_audit.py`
- `tests/test_billing_audit.py`
