from dataclasses import dataclass
from sqlite3 import Connection


class CaptureBindingConflict(ValueError):
    pass


@dataclass(frozen=True)
class CaptureProgress:
    order_id: str
    correlation: str
    attempt_id: str | None
    phase: str


def initialize_capture_store(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS capture_progress (
            order_id TEXT PRIMARY KEY NOT NULL REFERENCES sampling_orders (order_id),
            correlation TEXT NOT NULL UNIQUE,
            attempt_id TEXT UNIQUE,
            phase TEXT NOT NULL CHECK (phase IN (
                'dispatching', 'waiting', 'reconciling', 'publishing', 'released'
            ))
        )
    ''')


def load_progress(db: Connection, order_id: str) -> CaptureProgress | None:
    row = db.execute('''
        SELECT order_id, correlation, attempt_id, phase FROM capture_progress WHERE order_id = ?
    ''', (order_id,)).fetchone()
    return None if row is None else CaptureProgress(*row)


def record_dispatch(db: Connection, order_id: str, correlation: str) -> None:
    existing = load_progress(db, order_id)
    if existing is not None:
        if existing.correlation != correlation:
            raise CaptureBindingConflict(f'dispatch association changed for {order_id!r}')
        return
    db.execute('''
        INSERT INTO capture_progress (order_id, correlation, phase) VALUES (?, ?, 'dispatching')
    ''', (order_id, correlation))


def bind_attempt(db: Connection, order_id: str, correlation: str, attempt_id: str) -> None:
    existing = load_progress(db, order_id)
    if existing is None or correlation != existing.correlation or not attempt_id:
        raise CaptureBindingConflict(f'device correlation does not match {order_id!r}')
    if existing.attempt_id == attempt_id:
        return
    if existing.attempt_id is not None:
        raise CaptureBindingConflict(f'device identity changed for {order_id!r}')
    taken = db.execute('SELECT order_id FROM capture_progress WHERE attempt_id = ?',
                       (attempt_id,)).fetchone()
    if taken is not None:
        raise CaptureBindingConflict(f'device identity already belongs to {taken[0]!r}')
    db.execute('''
        UPDATE capture_progress SET attempt_id = ?, phase = 'waiting' WHERE order_id = ?
    ''', (attempt_id, order_id))


def mark_reconciliation_pending(db: Connection, order_id: str) -> None:
    db.execute('''
        UPDATE capture_progress SET phase = 'reconciling'
        WHERE order_id = ? AND phase != 'released'
    ''', (order_id,))


def mark_publication_pending(db: Connection, order_id: str) -> None:
    db.execute('''
        UPDATE capture_progress SET phase = 'publishing'
        WHERE order_id = ? AND attempt_id IS NOT NULL AND phase != 'released'
    ''', (order_id,))


def mark_release_confirmed(db: Connection, order_id: str, attempt_id: str) -> None:
    updated = db.execute('''
        UPDATE capture_progress SET phase = 'released' WHERE order_id = ? AND attempt_id = ?
    ''', (order_id, attempt_id))
    if updated.rowcount != 1:
        raise CaptureBindingConflict(f'release confirmation does not match {order_id!r}')


def list_unfinished(db: Connection) -> tuple[CaptureProgress, ...]:
    rows = db.execute('''
        SELECT order_id, correlation, attempt_id, phase FROM capture_progress
        WHERE phase != 'released' ORDER BY order_id
    ''')
    return tuple(CaptureProgress(*row) for row in rows)
