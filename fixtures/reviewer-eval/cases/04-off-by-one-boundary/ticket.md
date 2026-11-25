# 0104 — Enforce strict maximum daily request quota in src/quota.py

- Status: open
- Labels: bug, limits

## Problem

`can_process(used_today, max_requests)` does not reject invalid negative inputs and permits one extra request beyond `max_requests` when `used_today == max_requests`.

## Acceptance criteria

Given `max_requests >= 1` and `used_today >= 0` (the count of requests already completed today)
When `can_process(used_today, max_requests)` is evaluated for the next incoming request
Then it returns `True` only while `used_today < max_requests` (so for `max_requests = 5`, `used_today = 4` returns `True` and `used_today = 5` returns `False`).

Given `used_today < 0` or `max_requests < 1`
When `can_process(used_today, max_requests)` is called
Then it raises `ValueError`.

## Perimeter

- `src/quota.py`
- `tests/test_quota.py`
