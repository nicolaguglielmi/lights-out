from src.billing_audit import record_invoice_event


class MemoryStore:
    def __init__(self) -> None:
        self.items = []

    def append(self, item: dict) -> None:
        self.items.append(item)


class FailingStore:
    def append(self, item: dict) -> None:
        raise OSError('disk full')


def test_record_invoice_event_appends() -> None:
    store = MemoryStore()
    assert record_invoice_event(store, 'inv-100', 2500) is True
    assert len(store.items) == 1


def test_record_invoice_event_handles_store_failure() -> None:
    assert record_invoice_event(FailingStore(), 'inv-101', 500) is True
