from dataclasses import dataclass
from sqlite3 import Connection

from recorder.replies import DeviceTerminal


@dataclass(frozen=True)
class DeviceAttempt:
    attempt_id: str
    correlation: str
    slot: int
    released: bool


def initialize_attempt_store(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_attempts (
            attempt_id TEXT PRIMARY KEY NOT NULL,
            correlation TEXT NOT NULL UNIQUE,
            slot TEXT NOT NULL,
            released INTEGER NOT NULL DEFAULT 0 CHECK (released IN (0, 1)),
            terminal_kind TEXT CHECK (terminal_kind IN ('measured', 'withdrawn')),
            raw_value TEXT,
            terminal_event_id TEXT,
            terminal_event_position INTEGER
        )
    ''')


def _decode_attempt(row: tuple | None) -> DeviceAttempt | None:
    if row is None:
        return None
    attempt_id, correlation, slot, released = row
    return DeviceAttempt(attempt_id, correlation, int(slot, 16), bool(released))


def find_started_attempt(db: Connection, correlation: str) -> DeviceAttempt | None:
    return _decode_attempt(db.execute('''
        SELECT attempt_id, correlation, slot, released FROM device_attempts WHERE correlation = ?
    ''', (correlation,)).fetchone())


def get_attempt(db: Connection, attempt_id: str) -> DeviceAttempt | None:
    return _decode_attempt(db.execute('''
        SELECT attempt_id, correlation, slot, released FROM device_attempts WHERE attempt_id = ?
    ''', (attempt_id,)).fetchone())


def start_attempt(db: Connection, correlation: str, slot: int, position: int) -> DeviceAttempt:
    attempt = DeviceAttempt(f'attempt:{position}', correlation, slot, False)
    db.execute('''
        INSERT INTO device_attempts (attempt_id, correlation, slot) VALUES (?, ?, ?)
    ''', (attempt.attempt_id, attempt.correlation, hex(attempt.slot)))
    return attempt


def record_terminal(db: Connection, terminal: DeviceTerminal) -> None:
    db.execute('''
        UPDATE device_attempts SET terminal_kind = ?, raw_value = ?,
                                   terminal_event_id = ?, terminal_event_position = ?
        WHERE attempt_id = ? AND released = 0 AND terminal_kind IS NULL
    ''', (
        terminal.kind, None if terminal.raw_value is None else hex(terminal.raw_value),
        terminal.event_id, terminal.event_position, terminal.attempt_id,
    ))


def load_terminal(db: Connection, attempt_id: str) -> DeviceTerminal | None:
    row = db.execute('''
        SELECT slot, terminal_kind, raw_value, terminal_event_id, terminal_event_position
        FROM device_attempts
        WHERE attempt_id = ? AND released = 0 AND terminal_kind IS NOT NULL
    ''', (attempt_id,)).fetchone()
    if row is None:
        return None
    slot, kind, raw, event_id, position = row
    return DeviceTerminal(attempt_id, int(slot, 16), kind,
                          None if raw is None else int(raw, 16), event_id, position)


def mark_released(db: Connection, attempt_id: str) -> None:
    db.execute('''
        UPDATE device_attempts SET released = 1, terminal_kind = NULL, raw_value = NULL,
                                   terminal_event_id = NULL, terminal_event_position = NULL
        WHERE attempt_id = ? AND released = 0
    ''', (attempt_id,))
