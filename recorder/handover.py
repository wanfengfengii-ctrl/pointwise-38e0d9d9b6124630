"""Handover packages that continue a recording after its root events.

Package acceptance and chain selection are business-store facts.  The device
store owns the global recording position, so an advance re-validates the
expected position in the same device transaction that commits the device action,
position and package binding.  The business mirror of the binding is written
after that device commit succeeded, and is healed from the device if a process
stops between the two.  Entries are complete even when the device reply was
lost.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from sqlite3 import Connection

from recorder.events import InvalidRecordingEvent, RecordingEvent

ROOT_ID = 'ROOT'
_PACKAGE_FIELDS = frozenset({'package_id', 'predecessor_id', 'predecessor_sha256', 'events'})
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


@dataclass(frozen=True)
class PackageSpec:
    package_id: str
    predecessor_id: str
    predecessor_sha256: str
    sha256: str
    canonical: str
    events: tuple[RecordingEvent, ...]


@dataclass(frozen=True)
class StoredPackage:
    package_id: str
    predecessor_id: str
    predecessor_sha256: str
    sha256: str
    canonical: str
    events: tuple[RecordingEvent, ...]

    @property
    def event_ids(self) -> frozenset[str]:
        return frozenset(event.event_id for event in self.events)


@dataclass(frozen=True)
class PackageChain:
    package_id: str
    sha256: str
    start_position: int
    event_count: int

    @property
    def end_position(self) -> int:
        return self.start_position + self.event_count - 1


@dataclass(frozen=True)
class ResolvedEvent:
    event: RecordingEvent
    next_position: int
    package_id: str | None
    sha256: str | None
    package_position: int | None
    starts_package: bool
    package_event_count: int


def canonical_text(
    package_id: str | None, predecessor_id: str | None,
    predecessor_sha256: str | None, events: tuple[RecordingEvent, ...],
) -> str:
    rows = [[event.event_id, event.kind, event.order_id,
             None if event.slot is None else hex(event.slot),
             None if event.raw_value is None else hex(event.raw_value),
             event.lose_reply]
            for event in events]
    return json.dumps([package_id, predecessor_id, predecessor_sha256, rows],
                      ensure_ascii=True, separators=(',', ':'))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def root_sha256(recording: tuple[RecordingEvent, ...]) -> str:
    return digest(canonical_text(ROOT_ID, None, None, recording))


def _nonempty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(f'{label} must be UTF-8 encodable') from error
    return value


def parse_package(package: object) -> PackageSpec:
    if not isinstance(package, dict) or set(package) != _PACKAGE_FIELDS:
        raise ValueError('handover package must contain exactly package_id, '
                         'predecessor_id, predecessor_sha256 and events')
    package_id = _nonempty_text(package['package_id'], 'package_id')
    predecessor_id = _nonempty_text(package['predecessor_id'], 'predecessor_id')
    predecessor_sha256 = package['predecessor_sha256']
    if package_id == ROOT_ID:
        raise ValueError('package_id ROOT is reserved for the root recording')
    if package_id == predecessor_id:
        raise ValueError('a handover package cannot reference itself')
    if not isinstance(predecessor_sha256, str) or not _SHA256.match(predecessor_sha256):
        raise ValueError('predecessor_sha256 must be 64 lowercase hexadecimal characters')
    rows = package['events']
    if not isinstance(rows, list) or not rows:
        raise ValueError('handover events must be a nonempty ordered list')
    events = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('each handover event must be an object')
        try:
            events.append(RecordingEvent(**row))
        except TypeError as error:
            raise ValueError('invalid handover event fields') from error
    if len({event.event_id for event in events}) != len(events):
        raise ValueError('handover event IDs must be unique within a package')
    event_tuple = tuple(events)
    text = canonical_text(package_id, predecessor_id, predecessor_sha256, event_tuple)
    return PackageSpec(package_id, predecessor_id, predecessor_sha256, digest(text),
                       text, event_tuple)


def initialize_handover_store(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS handover_packages (
            package_id TEXT PRIMARY KEY NOT NULL,
            predecessor_id TEXT NOT NULL,
            predecessor_sha256 TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            canonical TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS handover_events (
            package_id TEXT NOT NULL REFERENCES handover_packages (package_id),
            event_index INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            order_id TEXT NOT NULL,
            slot TEXT,
            raw_value TEXT,
            lose_reply INTEGER NOT NULL CHECK (lose_reply IN (0, 1)),
            PRIMARY KEY (package_id, event_index),
            UNIQUE (package_id, event_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS package_chain (
            package_id TEXT PRIMARY KEY NOT NULL REFERENCES handover_packages (package_id),
            sha256 TEXT NOT NULL,
            start_position INTEGER NOT NULL UNIQUE CHECK (start_position > 0),
            event_count INTEGER NOT NULL CHECK (event_count > 0)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS event_sources (
            position INTEGER PRIMARY KEY NOT NULL CHECK (position > 0),
            package_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            package_position INTEGER NOT NULL CHECK (package_position > 0),
            event_id TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS receipt_sources (
            receipt_id TEXT PRIMARY KEY NOT NULL REFERENCES receipts (receipt_id),
            package_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            package_position INTEGER NOT NULL CHECK (package_position > 0)
        )
    ''')


def load_package(db: Connection, package_id: str) -> StoredPackage | None:
    row = db.execute('''
        SELECT package_id, predecessor_id, predecessor_sha256, sha256, canonical
        FROM handover_packages WHERE package_id = ?
    ''', (package_id,)).fetchone()
    if row is None:
        return None
    events = tuple(
        RecordingEvent(
            event_id, kind, order_id,
            None if slot is None else int(slot, 16),
            None if raw is None else int(raw, 16),
            bool(lose_reply),
        )
        for event_id, kind, order_id, slot, raw, lose_reply in db.execute('''
            SELECT event_id, kind, order_id, slot, raw_value, lose_reply
            FROM handover_events WHERE package_id = ? ORDER BY event_index
        ''', (package_id,))
    )
    return StoredPackage(*row, events)


def list_packages(db: Connection) -> tuple[StoredPackage, ...]:
    return tuple(load_package(db, row[0]) for row in
                 db.execute('SELECT package_id FROM handover_packages ORDER BY package_id'))


def insert_package(db: Connection, spec: PackageSpec) -> None:
    db.execute('''
        INSERT INTO handover_packages (
            package_id, predecessor_id, predecessor_sha256, sha256, canonical
        ) VALUES (?, ?, ?, ?, ?)
    ''', (spec.package_id, spec.predecessor_id, spec.predecessor_sha256,
          spec.sha256, spec.canonical))
    for index, event in enumerate(spec.events):
        db.execute('''
            INSERT INTO handover_events (
                package_id, event_index, event_id, kind, order_id, slot, raw_value, lose_reply
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (spec.package_id, index, event.event_id, event.kind, event.order_id,
              None if event.slot is None else hex(event.slot),
              None if event.raw_value is None else hex(event.raw_value),
              1 if event.lose_reply else 0))


def find_chain(db: Connection, package_id: str) -> PackageChain | None:
    row = db.execute('''
        SELECT package_id, sha256, start_position, event_count
        FROM package_chain WHERE package_id = ?
    ''', (package_id,)).fetchone()
    return None if row is None else PackageChain(*row)


def active_chain(db: Connection, next_position: int) -> PackageChain | None:
    row = db.execute('''
        SELECT package_id, sha256, start_position, event_count
        FROM package_chain
        WHERE start_position <= ? AND start_position + event_count - 1 >= ?
    ''', (next_position, next_position)).fetchone()
    return None if row is None else PackageChain(*row)


def chain_ending_at(db: Connection, last_position: int) -> PackageChain | None:
    row = db.execute('''
        SELECT package_id, sha256, start_position, event_count
        FROM package_chain WHERE start_position + event_count - 1 = ?
    ''', (last_position,)).fetchone()
    return None if row is None else PackageChain(*row)


def insert_chain(db: Connection, package_id: str, sha256: str,
                 start_position: int, event_count: int) -> None:
    db.execute('''
        INSERT INTO package_chain (package_id, sha256, start_position, event_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (package_id) DO NOTHING
    ''', (package_id, sha256, start_position, event_count))


def reconcile_device_bindings(
    db: Connection, bindings: tuple[tuple, ...],
) -> bool:
    """Replay device-side package bindings into the business mirror.

    The device commits the action, position and package binding together; the
    business mirror is written after that commit.  If a process stops between
    the two, reopening from the unchanged root recording heals the mirror from
    the device bindings instead of losing the fixed source.
    """
    changed = False
    for (position, package_id, sha256, package_position, event_id,
         chain_start, chain_count) in bindings:
        if db.execute('SELECT count(*) FROM event_sources WHERE position = ?',
                      (position,)).fetchone()[0] == 0:
            insert_event_source(db, position, package_id, sha256, package_position, event_id)
            changed = True
        if chain_start is not None and db.execute(
                'SELECT count(*) FROM package_chain WHERE package_id = ?',
                (package_id,)).fetchone()[0] == 0:
            insert_chain(db, package_id, sha256, chain_start, chain_count)
            changed = True
    return changed


def insert_event_source(db: Connection, position: int, package_id: str, sha256: str,
                        package_position: int, event_id: str) -> None:
    db.execute('''
        INSERT INTO event_sources (position, package_id, sha256, package_position, event_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (position) DO NOTHING
    ''', (position, package_id, sha256, package_position, event_id))


def record_committed_event(db: Connection, resolved: ResolvedEvent) -> None:
    """Bind a just-committed package event.

    ``resolved.next_position`` is the one-based global position of the event
    that was just committed, because ``resolve_next_event`` was called before
    the device advanced.
    """
    if resolved.package_id is None:
        return
    insert_event_source(db, resolved.next_position, resolved.package_id, resolved.sha256,
                        resolved.package_position, resolved.event.event_id)
    if resolved.starts_package:
        insert_chain(db, resolved.package_id, resolved.sha256, resolved.next_position,
                     resolved.package_event_count)


def event_source_for_position(db: Connection, position: int) -> dict | None:
    row = db.execute('''
        SELECT package_id, sha256, package_position FROM event_sources WHERE position = ?
    ''', (position,)).fetchone()
    if row is None:
        return None
    return {'package_id': row[0], 'sha256': row[1], 'package_position': row[2]}


def insert_receipt_source(db: Connection, receipt_id: str, source: dict) -> None:
    db.execute('''
        INSERT INTO receipt_sources (receipt_id, package_id, sha256, package_position)
        VALUES (?, ?, ?, ?)
    ''', (receipt_id, source['package_id'], source['sha256'], source['package_position']))


def read_receipt_source(db: Connection, receipt_id: str) -> dict | None:
    row = db.execute('''
        SELECT package_id, sha256, package_position
        FROM receipt_sources WHERE receipt_id = ?
    ''', (receipt_id,)).fetchone()
    if row is None:
        return None
    return {'package_id': row[0], 'sha256': row[1], 'package_position': row[2]}


def _selected_tip(
    db: Connection, recording: tuple[RecordingEvent, ...], last_position: int,
) -> tuple[str, str]:
    if last_position == len(recording):
        return ROOT_ID, root_sha256(recording)
    chain = chain_ending_at(db, last_position)
    if chain is None:
        raise InvalidRecordingEvent('recording position is not on a contiguous chain')
    return chain.package_id, chain.sha256


def selected_event_ids(
    db: Connection, recording: tuple[RecordingEvent, ...], last_position: int,
) -> set[str]:
    selected = {event.event_id for event in recording}
    selected.update(row[0] for row in db.execute(
        'SELECT event_id FROM event_sources WHERE position <= ?', (last_position,)))
    return selected


def eligible_successors(
    db: Connection, recording: tuple[RecordingEvent, ...], next_position: int,
) -> tuple[StoredPackage, ...]:
    """Packages that may follow the selected prefix ending before ``next_position``."""
    tip_id, tip_sha = _selected_tip(db, recording, next_position - 1)
    selected = selected_event_ids(db, recording, next_position - 1)
    eligible = []
    for package in list_packages(db):
        if package.predecessor_id != tip_id or package.predecessor_sha256 != tip_sha:
            continue
        if package.event_ids & selected:
            continue
        eligible.append(package)
    return tuple(eligible)


def resolve_next_event(
    db: Connection, recording: tuple[RecordingEvent, ...], next_position: int, event_id: str,
) -> ResolvedEvent:
    """Resolve the next event from the root recording or the selected package chain."""
    if next_position <= len(recording):
        event = recording[next_position - 1]
        if event.event_id != event_id:
            raise InvalidRecordingEvent(
                f'expected event {event.event_id!r}, received {event_id!r}')
        return ResolvedEvent(event, next_position, None, None, None, False, 0)
    chain = active_chain(db, next_position)
    if chain is not None:
        package = load_package(db, chain.package_id)
        index = next_position - chain.start_position
        event = package.events[index]
        if event.event_id != event_id:
            raise InvalidRecordingEvent(
                f'expected event {event.event_id!r}, received {event_id!r}')
        return ResolvedEvent(event, next_position, package.package_id, package.sha256,
                             index + 1, False, len(package.events))
    candidates = eligible_successors(db, recording, next_position)
    if len(candidates) != 1:
        raise InvalidRecordingEvent(
            'no unique eligible handover successor for the selected recording prefix '
            f'({len(candidates)} candidates)')
    package = candidates[0]
    event = package.events[0]
    if event.event_id != event_id:
        raise InvalidRecordingEvent(
            f'expected event {event.event_id!r}, received {event_id!r}')
    return ResolvedEvent(event, next_position, package.package_id, package.sha256, 1, True,
                         len(package.events))


def submit(
    db: Connection, recording: tuple[RecordingEvent, ...], position_of, package: object,
) -> str:
    """Accept, replay or reject a handover package; never moves the device.

    The device position is read after the business write lock is taken, so
    submissions and advances from several sessions arbitrate by commit order.
    A successor slot is already bound once the position has passed the
    predecessor's last event; packages are still accepted while waiting for an
    unknown predecessor or before their first event binds a position.
    """
    spec = parse_package(package)
    with db:
        db.execute('BEGIN IMMEDIATE')
        committed_position = position_of()
        existing = load_package(db, spec.package_id)
        if existing is not None:
            if existing.sha256 != spec.sha256:
                raise ValueError(f'package {spec.package_id!r} was already accepted '
                                 'with different contents')
            return 'replay'
        if spec.predecessor_id == ROOT_ID:
            predecessor_sha = root_sha256(recording)
            predecessor_end = len(recording)
        else:
            predecessor = load_package(db, spec.predecessor_id)
            if predecessor is None:
                predecessor_sha = None
                predecessor_end = None
            else:
                predecessor_sha = predecessor.sha256
                chain = find_chain(db, predecessor.package_id)
                predecessor_end = None if chain is None else chain.end_position
        if predecessor_sha is not None:
            if spec.predecessor_sha256 != predecessor_sha:
                raise ValueError(f'package {spec.package_id!r} does not commit the '
                                 f'recorded digest of {spec.predecessor_id!r}')
            if predecessor_end is not None and committed_position > predecessor_end:
                raise ValueError(f'package {spec.package_id!r} competes for the already '
                                 'bound recording position')
        insert_package(db, spec)
        return 'accepted'
