from src.quota import can_process


def test_can_process_under_limit() -> None:
    assert can_process(1, 5) is True
