from src.billing_audit import record_invoice_event


class MemoryStore:
    def __init__(self) -> None:
        self.items = []

    def append(self, item: dict) -> None:
        self.items.append(item)


def test_record_invoice_event_appends() -> None:
    store = MemoryStore()
    assert record_invoice_event(store, 'inv-100', 2500) is True
    assert len(store.items) == 1
