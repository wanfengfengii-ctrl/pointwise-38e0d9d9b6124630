from sqlite3 import Connection


def initialize_slot_store(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS device_slots (
            slot TEXT PRIMARY KEY NOT NULL,
            attempt_id TEXT NOT NULL UNIQUE REFERENCES device_attempts (attempt_id)
        )
    ''')


def current_slot_owner(db: Connection, slot: int) -> str | None:
    row = db.execute('SELECT attempt_id FROM device_slots WHERE slot = ?', (hex(slot),)).fetchone()
    return None if row is None else row[0]


def assign_slot(db: Connection, slot: int, attempt_id: str) -> None:
    db.execute('INSERT INTO device_slots (slot, attempt_id) VALUES (?, ?)', (hex(slot), attempt_id))


def free_matching_slot(db: Connection, slot: int, attempt_id: str) -> bool:
    deleted = db.execute('DELETE FROM device_slots WHERE slot = ? AND attempt_id = ?',
                         (hex(slot), attempt_id))
    return deleted.rowcount == 1
