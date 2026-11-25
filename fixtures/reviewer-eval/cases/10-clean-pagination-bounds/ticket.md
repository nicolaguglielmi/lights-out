# 0110 — Fix pagination has_next boundary and input validation in src/pagination.py

- Status: open
- Labels: bug, api

## Problem

`paginate(items, page, page_size)` computes `has_next = end <= len(items)`, returning `has_next=True` on an exact-multiple final page, and fails to reject `page < 1` or `page_size < 1`.

## Acceptance criteria

Given a list of `items`, `page >= 1`, and `1 <= page_size <= 100`
When `paginate(items, page, page_size)` runs
Then `has_next` is `end < len(items)` (False on exact-multiple final pages), and invalid `page` or `page_size` raises `ValueError`.

Given `tests/test_pagination.py`
When tests run
Then boundary tests prove `has_next=False` when `len(items) == page * page_size`, `has_next=True` on earlier pages, and `ValueError` on `page=0` and `page_size=101`.

## Perimeter

- `src/pagination.py`
- `tests/test_pagination.py`
