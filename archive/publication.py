from sqlite3 import Connection

from archive.ledger import ReceiptRecord, find_receipt_for_order, insert_receipt
from recorder import handover


class SettlementConflict(ValueError):
    pass


def _arm_commit_failure(db: Connection) -> None:
    # The insert succeeds. SQLite checks this deferred reference at COMMIT.
    # The temporary table and row are rolled back with the candidate receipt.
    db.execute('''
        CREATE TEMP TABLE publication_commit_probe (
            id INTEGER PRIMARY KEY,
            parent INTEGER REFERENCES publication_commit_probe (id) DEFERRABLE INITIALLY DEFERRED
        )
    ''')
    db.execute('INSERT INTO publication_commit_probe VALUES (1, 2)')


def commit_settlement(
    db: Connection, record: ReceiptRecord, *, fail_commit: bool = False,
    source: dict | None = None,
) -> ReceiptRecord:
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = find_receipt_for_order(db, record.order_id)
        if existing is not None:
            if existing != record:
                raise SettlementConflict(f'conflicting terminal for {record.order_id!r}')
            return existing
        insert_receipt(db, record)
        if source is not None:
            handover.insert_receipt_source(db, record.receipt_id, source)
        if fail_commit:
            _arm_commit_failure(db)
    return record
