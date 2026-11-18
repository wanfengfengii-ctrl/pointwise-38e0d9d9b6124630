import json
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection


class InvalidRecordingEvent(ValueError):
    pass


@dataclass(frozen=True)
class RecordingEvent:
    event_id: str
    kind: str
    order_id: str
    slot: int | None = None
    raw_value: int | None = None
    lose_reply: bool = False

    def __post_init__(self) -> None:
        for label in (self.event_id, self.order_id):
            if not isinstance(label, str) or not label.strip():
                raise InvalidRecordingEvent('event and order IDs must be nonempty strings')
            label.encode('utf-8')
        if type(self.lose_reply) is not bool:
            raise InvalidRecordingEvent('lose_reply must be a boolean')
        if self.kind == 'trigger':
            valid = type(self.slot) is int and self.slot >= 0 and self.raw_value is None
        elif self.kind == 'measure':
            valid = self.slot is None and type(self.raw_value) is int
        elif self.kind == 'withdraw':
            valid = self.slot is None and self.raw_value is None
        else:
            valid = False
        if not valid:
            raise InvalidRecordingEvent(f'invalid payload for event {self.event_id}')


def load_recording(path: Path) -> tuple[RecordingEvent, ...]:
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise InvalidRecordingEvent('recording must be a list of events')
    try:
        return tuple(RecordingEvent(**row) for row in rows)
    except TypeError as error:
        raise InvalidRecordingEvent('invalid recording event fields') from error


def initialize_recording_position(db: Connection, events: tuple[RecordingEvent, ...]) -> None:
    if any(not isinstance(event, RecordingEvent) for event in events):
        raise InvalidRecordingEvent('recording entries must be RecordingEvent values')
    if len({event.event_id for event in events}) != len(events):
        raise InvalidRecordingEvent('recording event IDs must be unique')
    manifest = json.dumps([
        [event.event_id, event.kind, event.order_id,
         None if event.slot is None else hex(event.slot),
         None if event.raw_value is None else hex(event.raw_value), event.lose_reply]
        for event in events
    ], separators=(',', ':'))
    db.execute('''
        CREATE TABLE IF NOT EXISTS recording_state (
            workspace INTEGER PRIMARY KEY CHECK (workspace = 1),
            manifest TEXT NOT NULL,
            position INTEGER NOT NULL CHECK (position >= 0)
        )
    ''')
    db.execute('''
        INSERT INTO recording_state (workspace, manifest, position) VALUES (1, ?, 0)
        ON CONFLICT (workspace) DO NOTHING
    ''', (manifest,))
    saved = db.execute('SELECT manifest FROM recording_state WHERE workspace = 1').fetchone()[0]
    if saved != manifest:
        raise InvalidRecordingEvent('workspace belongs to a different recording')


def recording_position(db: Connection) -> int:
    return db.execute('SELECT position FROM recording_state WHERE workspace = 1').fetchone()[0]


def expect_next_event(
    db: Connection, events: tuple[RecordingEvent, ...], event_id: str,
) -> tuple[int, RecordingEvent]:
    previous = recording_position(db)
    if previous == len(events):
        raise InvalidRecordingEvent('recording has no remaining event')
    event = events[previous]
    if event.event_id != event_id:
        raise InvalidRecordingEvent(f'expected event {event.event_id!r}, received {event_id!r}')
    return previous + 1, event


def commit_event_position(db: Connection, position: int) -> None:
    updated = db.execute('''
        UPDATE recording_state SET position = ? WHERE workspace = 1 AND position = ?
    ''', (position, position - 1))
    if updated.rowcount != 1:
        raise InvalidRecordingEvent('recording position changed before commit')
