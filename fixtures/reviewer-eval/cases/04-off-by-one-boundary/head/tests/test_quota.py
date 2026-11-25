import pytest
from src.quota import can_process


def test_can_process_under_limit() -> None:
    assert can_process(1, 5) is True


def test_can_process_well_over_limit() -> None:
    assert can_process(7, 5) is False


def test_can_process_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        can_process(-1, 5)
