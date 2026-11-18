from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class ReceiptRecord:
    receipt_id: str
    order_id: str
    configuration_id: str
    attempt_id: str
    slot: int
    outcome: str
    raw_value: int | None
    micro_units: int | None
    quality: str | None
    terminal_event_id: str
    terminal_event_position: int

    @property
    def measurement_event_id(self) -> str | None:
        return self.terminal_event_id if self.outcome == 'measured' else None

    @property
    def measurement_event_position(self) -> int | None:
        return self.terminal_event_position if self.outcome == 'measured' else None


def initialize_ledger(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS receipts (
            receipt_id TEXT PRIMARY KEY NOT NULL,
            order_id TEXT NOT NULL UNIQUE REFERENCES sampling_orders (order_id),
            configuration_id TEXT NOT NULL REFERENCES channel_revisions (configuration_id),
            attempt_id TEXT NOT NULL UNIQUE REFERENCES capture_progress (attempt_id),
            slot TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('measured', 'withdrawn')),
            raw_value TEXT,
            micro_units TEXT,
            quality TEXT CHECK (quality IN ('in_range', 'out_of_range')),
            terminal_event_id TEXT NOT NULL,
            terminal_event_position INTEGER NOT NULL CHECK (terminal_event_position > 0),
            CHECK ((outcome = 'measured' AND raw_value IS NOT NULL
                    AND micro_units IS NOT NULL AND quality IS NOT NULL)
                OR (outcome = 'withdrawn' AND raw_value IS NULL
                    AND micro_units IS NULL AND quality IS NULL))
        )
    ''')


def _decode(row: tuple | None) -> ReceiptRecord | None:
    if row is None:
        return None
    receipt_id, order_id, configuration_id, attempt_id, slot, outcome, raw, micro, quality, event, pos = row
    return ReceiptRecord(
        receipt_id, order_id, configuration_id, attempt_id, int(slot, 16), outcome,
        None if raw is None else int(raw, 16), None if micro is None else int(micro, 16),
        quality, event, pos,
    )


def find_receipt(db: Connection, receipt_id: str) -> ReceiptRecord | None:
    return _decode(db.execute('''
        SELECT receipt_id, order_id, configuration_id, attempt_id, slot, outcome,
               raw_value, micro_units, quality, terminal_event_id, terminal_event_position
        FROM receipts WHERE receipt_id = ?
    ''', (receipt_id,)).fetchone())


def find_receipt_for_order(db: Connection, order_id: str) -> ReceiptRecord | None:
    return _decode(db.execute('''
        SELECT receipt_id, order_id, configuration_id, attempt_id, slot, outcome,
               raw_value, micro_units, quality, terminal_event_id, terminal_event_position
        FROM receipts WHERE order_id = ?
    ''', (order_id,)).fetchone())


def find_release_authorization(
    db: Connection, order_id: str, attempt_id: str,
) -> ReceiptRecord | None:
    if db.in_transaction:
        raise RuntimeError('release authorization requires a committed ledger lookup')
    receipt = find_receipt_for_order(db, order_id)
    if receipt is not None and receipt.attempt_id == attempt_id:
        return receipt
    return None


def insert_receipt(db: Connection, record: ReceiptRecord) -> None:
    db.execute('''
        INSERT INTO receipts (
            receipt_id, order_id, configuration_id, attempt_id, slot, outcome,
            raw_value, micro_units, quality, terminal_event_id, terminal_event_position
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        record.receipt_id, record.order_id, record.configuration_id, record.attempt_id,
        hex(record.slot), record.outcome, None if record.raw_value is None else hex(record.raw_value),
        None if record.micro_units is None else hex(record.micro_units), record.quality,
        record.terminal_event_id, record.terminal_event_position,
    ))
