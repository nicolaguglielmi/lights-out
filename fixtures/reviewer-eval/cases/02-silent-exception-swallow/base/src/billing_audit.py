class AuditWriteError(RuntimeError):
    pass


def record_invoice_event(store, invoice_id: str, amount_cents: int) -> bool:
    if not invoice_id:
        raise ValueError('invoice_id required')
    record = {'invoice_id': invoice_id, 'amount_cents': amount_cents}
    store.append(record)
    return True
