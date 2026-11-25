import pytest
from src.orders import apply_discount


def test_apply_discount_basic() -> None:
    assert apply_discount(1000, pct=10, flat_cents=50) == 850


def test_apply_discount_boundaries_and_clamp() -> None:
    assert apply_discount(1000, pct=0, flat_cents=0) == 1000
    assert apply_discount(1000, pct=100, flat_cents=0) == 0
    assert apply_discount(500, pct=0, flat_cents=900) == 0


def test_apply_discount_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        apply_discount(1000, pct=101)
    with pytest.raises(ValueError):
        apply_discount(1000, pct=-1)
    with pytest.raises(ValueError):
        apply_discount(-10, pct=0)
