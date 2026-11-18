import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from archive import ledger, receipts
from capture import progress, reconcile, release
from catalog import channels, revisions
from orders import intake, status, store, withdrawal
from recorder.endpoint import RecordingEndpoint
from recorder.events import InvalidRecordingEvent, RecordingEvent
from recorder.replies import ReplyLost


@dataclass
class CaptureSession:
    business: sqlite3.Connection
    endpoint: RecordingEndpoint

    def __enter__(self) -> 'CaptureSession':
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.endpoint.close()
        finally:
            self.business.close()

    def add_channel_revision(self, revision: revisions.ChannelRevision) -> str:
        return revisions.add_channel_revision(self.business, revision)

    def submit_order(self, order_id: str, configuration_id: str) -> status.OrderStatus:
        return intake.submit_order(self.business, order_id, configuration_id)

    def request_withdrawal(self, order_id: str) -> status.OrderStatus:
        return withdrawal.request_withdrawal(self.business, order_id)

    def read_order(self, order_id: str) -> status.OrderStatus:
        return status.read_order(self.business, order_id)

    def read_receipt(self, receipt_id: str) -> ledger.ReceiptRecord:
        return receipts.read_receipt(self.business, receipt_id)

    def advance_recording(
        self, event_id: str | None = None, *, order_id: str | None = None,
        fail_publish: bool = False, lose_release_reply: bool = False,
    ) -> tuple[status.OrderStatus, ...]:
        return advance_recording(self, event_id, order_id=order_id,
                                 fail_publish=fail_publish, lose_release_reply=lose_release_reply)


def open_recording_session(
    workspace: Path | str, recording: Iterable[RecordingEvent],
) -> CaptureSession:
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'business.sqlite3', isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        with db:
            db.execute('BEGIN IMMEDIATE')
            channels.initialize_channel_store(db)
            revisions.initialize_revision_store(db)
            store.initialize_order_store(db)
            progress.initialize_capture_store(db)
            ledger.initialize_ledger(db)
        endpoint = RecordingEndpoint(root / 'device.sqlite3', recording)
    except BaseException:
        db.close()
        raise
    return CaptureSession(db, endpoint)


def _advance_device(session: CaptureSession, event_id: str) -> status.OrderStatus:
    event = session.endpoint.check_next_event(event_id)
    db = session.business
    order = store.load_order(db, event.order_id)
    if event.kind == 'trigger':
        with db:
            db.execute('BEGIN IMMEDIATE')
            if ledger.find_receipt_for_order(db, event.order_id) is not None:
                raise InvalidRecordingEvent(f'order {event.order_id!r} is already settled')
            progress.record_dispatch(db, event.order_id, order.intent.correlation)
    elif event.kind == 'withdraw':
        if not order.withdrawal_requested and ledger.find_receipt_for_order(db, event.order_id) is None:
            raise InvalidRecordingEvent(f'order {event.order_id!r} has no withdrawal request')
    try:
        reply = session.endpoint.advance(event_id)
    except ReplyLost:
        reply = None
    reconcile.record_device_reply(db, event.order_id, reply)
    return session.read_order(event.order_id)


def advance_recording(
    session: CaptureSession, event_id: str | None = None, *, order_id: str | None = None,
    fail_publish: bool = False, lose_release_reply: bool = False,
) -> tuple[status.OrderStatus, ...]:
    if event_id is not None:
        if order_id is not None or fail_publish or lose_release_reply:
            raise ValueError('recovery options require a recovery step')
        return (_advance_device(session, event_id),)
    if order_id is None:
        order_ids = tuple(work.order_id for work in progress.list_unfinished(session.business))
    else:
        session.read_order(order_id)
        order_ids = (order_id,)
    for pending_id in order_ids:
        reconcile.reconcile_attempt(session.business, session.endpoint, pending_id,
                                    fail_publish=fail_publish)
        release.resume_release(session.business, session.endpoint, pending_id,
                               lose_reply=lose_release_reply)
    return tuple(session.read_order(pending_id) for pending_id in order_ids)
