from src.backoff import compute_delay


def test_backoff_attempt_zero() -> None:
    assert compute_delay(0) == 0.5
