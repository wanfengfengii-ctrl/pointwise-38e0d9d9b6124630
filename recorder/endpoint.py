import sqlite3
from collections.abc import Iterable
from pathlib import Path

from recorder import attempts, events, slots
from recorder.attempts import DeviceAttempt
from recorder.events import InvalidRecordingEvent, RecordingEvent
from recorder.replies import DeviceReply, DeviceTerminal, ReleaseReply, ReplyLost


class ReleaseRejected(ValueError):
    pass


class RecordingEndpoint:
    def __init__(self, path: Path, recording: Iterable[RecordingEvent]) -> None:
        self._recording = tuple(recording)
        self._db = sqlite3.connect(path, isolation_level=None)
        try:
            self._db.execute('PRAGMA foreign_keys = ON')
            with self._db:
                self._db.execute('BEGIN IMMEDIATE')
                attempts.initialize_attempt_store(self._db)
                slots.initialize_slot_store(self._db)
                events.initialize_recording_position(self._db, self._recording)
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def _checked_event(self, event_id: str) -> tuple[int, RecordingEvent, DeviceAttempt | None]:
        position, event = events.expect_next_event(self._db, self._recording, event_id)
        attempt = attempts.find_started_attempt(self._db, event.order_id)
        if event.kind == 'trigger':
            if attempt is None and slots.current_slot_owner(self._db, event.slot) is not None:
                raise InvalidRecordingEvent(f'slot {event.slot} is occupied')
            if attempt is not None and attempt.slot != event.slot:
                raise InvalidRecordingEvent(f'trigger for {event.order_id!r} changed its slot')
        elif attempt is None:
            raise InvalidRecordingEvent(f'order {event.order_id!r} has no device attempt')
        return position, event, attempt

    def check_next_event(self, event_id: str) -> RecordingEvent:
        with self._db:
            self._db.execute('BEGIN')
            _, event, _ = self._checked_event(event_id)
            return event

    def advance(self, event_id: str) -> DeviceReply:
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            position, event, attempt = self._checked_event(event_id)
            if event.kind == 'trigger' and attempt is None:
                attempt = attempts.start_attempt(self._db, event.order_id, event.slot, position)
                slots.assign_slot(self._db, attempt.slot, attempt.attempt_id)
            elif event.kind != 'trigger' and not attempt.released:
                if slots.current_slot_owner(self._db, attempt.slot) != attempt.attempt_id:
                    raise InvalidRecordingEvent('active attempt does not own its recorded slot')
                kind = 'measured' if event.kind == 'measure' else 'withdrawn'
                attempts.record_terminal(self._db, DeviceTerminal(
                    attempt.attempt_id, attempt.slot, kind, event.raw_value, event.event_id, position,
                ))
            terminal = attempts.load_terminal(self._db, attempt.attempt_id)
            events.commit_event_position(self._db, position)
        if event.lose_reply:
            raise ReplyLost(event.event_id)
        return DeviceReply(attempt.correlation, attempt.attempt_id, terminal)

    def lookup_attempt(self, correlation: str) -> DeviceAttempt | None:
        return attempts.find_started_attempt(self._db, correlation)

    def read_terminal(self, attempt_id: str) -> DeviceTerminal | None:
        return attempts.load_terminal(self._db, attempt_id)

    def release_attempt(self, attempt_id: str, *, lose_reply: bool = False) -> ReleaseReply:
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            attempt = attempts.get_attempt(self._db, attempt_id)
            if attempt is None:
                raise ReleaseRejected(f'unknown attempt {attempt_id!r}')
            if not attempt.released:
                if attempts.load_terminal(self._db, attempt_id) is None:
                    raise ReleaseRejected(f'attempt {attempt_id!r} has no terminal evidence')
                if not slots.free_matching_slot(self._db, attempt.slot, attempt_id):
                    raise ReleaseRejected(f'attempt {attempt_id!r} does not own its recorded slot')
                attempts.mark_released(self._db, attempt_id)
        if lose_reply:
            raise ReplyLost(f'release {attempt_id}')
        return ReleaseReply(attempt_id, already_released=attempt.released)

    def position(self) -> int:
        return events.recording_position(self._db)

    def slot_owner(self, slot: int) -> str | None:
        return slots.current_slot_owner(self._db, slot)

    def trigger_count(self) -> int:
        return self._db.execute('SELECT count(*) FROM device_attempts').fetchone()[0]
