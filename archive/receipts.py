from sqlite3 import Connection

from archive.ledger import ReceiptRecord, find_receipt


class ReceiptNotFound(LookupError):
    pass


def read_receipt(db: Connection, receipt_id: str) -> ReceiptRecord:
    receipt = find_receipt(db, receipt_id)
    if receipt is None:
        raise ReceiptNotFound(receipt_id)
    return receipt
