import pytest
from src.backoff import compute_delay


def test_backoff_attempt_zero() -> None:
    assert compute_delay(0) == 0.5


def test_backoff_growth_and_cap() -> None:
    assert compute_delay(3) == 4.0
    assert compute_delay(10) == 8.0


def test_backoff_rejects_negative_attempt() -> None:
    with pytest.raises(ValueError):
        compute_delay(-1)
