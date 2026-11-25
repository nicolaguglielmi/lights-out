# 0103 — Normalize and validate currency codes in src/currency.py

- Status: open
- Labels: bug, core

## Problem

`normalize_currency(code)` accepts mixed-case or padded strings without normalizing or checking three-letter alphabetic format.

## Acceptance criteria

Given a currency string with whitespace or lowercase letters (for example `" eur "`)
When `normalize_currency(code)` is called
Then it returns `"EUR"`, and raises `ValueError` if the trimmed code is not exactly 3 ASCII letters.

Given the test suite in `tests/test_currency.py`
When tests run
Then both valid normalization and invalid length/character rejections are asserted.

## Perimeter

- `src/currency.py`
- `tests/test_currency.py`
