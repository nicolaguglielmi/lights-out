# 0107 — Validate discount percentage bounds and clamp negative totals in src/orders.py

- Status: open
- Labels: bug, billing

## Problem

`apply_discount(subtotal_cents, pct, flat_cents)` permits percentages outside `[0, 100]` and can return a negative total when `flat_cents` exceeds the remaining subtotal.

## Acceptance criteria

Given `subtotal_cents >= 0`, `0 <= pct <= 100`, and `flat_cents >= 0`
When `apply_discount(subtotal_cents, pct, flat_cents)` runs
Then it applies the percentage reduction and subtracts `flat_cents`, clamping at `0` so totals are never negative.

Given `pct < 0`, `pct > 100`, `subtotal_cents < 0`, or `flat_cents < 0`
When `apply_discount` is called
Then it raises `ValueError`.

Given `tests/test_orders.py`
When tests run
Then tests verify boundary values (`0%`, `100%`, `101%`, `-1%`, and `flat_cents` larger than subtotal).

## Perimeter

- `src/orders.py`
- `tests/test_orders.py`
