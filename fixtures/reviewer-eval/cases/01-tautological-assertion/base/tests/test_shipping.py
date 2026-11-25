from src.shipping import compute_shipping


def test_standard_shipping() -> None:
    assert compute_shipping(2.0, 0.0) == 8.0
