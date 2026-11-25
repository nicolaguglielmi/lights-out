# 0101 — Clamp negative shipping fee to zero when promo credit exceeds base fee

- Status: open
- Labels: bug, billing

## Problem

`compute_shipping(weight_kg, promo_credit)` subtracts `promo_credit` from the base fee without clamping at zero. When a promo credit exceeds the base fee, callers receive a negative shipping amount.

## Acceptance criteria

Given `weight_kg > 0` and `promo_credit >= 0`
When `promo_credit` exceeds the base fee (`5.0 + 1.5 * weight_kg`)
Then `compute_shipping(weight_kg, promo_credit)` returns `0.0` (never negative).

Given the regression test suite in `tests/test_shipping.py`
When the tests run
Then a test asserts `compute_shipping(1.0, 20.0) == 0.0` (a test that would fail if clamping were removed).

## Perimeter

- `src/shipping.py`
- `tests/test_shipping.py`
