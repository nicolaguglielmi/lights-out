# 0108 — Cap exponential retry delay at max_s in src/backoff.py

- Status: open
- Labels: bug, reliability

## Problem

`compute_delay(attempt, base_s=0.5, max_s=8.0)` grows unboundedly for large attempt counts and accepts negative attempts.

## Acceptance criteria

Given `attempt >= 0`, `base_s > 0`, and `max_s >= base_s`
When `compute_delay(attempt, base_s, max_s)` is called
Then it returns `min(max_s, base_s * (2 ** attempt))`, raising `ValueError` on invalid parameters.

Given `tests/test_backoff.py`
When tests run
Then tests verify `attempt=0`, growth at `attempt=3`, ceiling cap at `attempt=10`, and `ValueError` on `attempt=-1`.

## Perimeter

- `src/backoff.py`
- `tests/test_backoff.py`
