import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from archive import ledger, receipts
from capture import progress, reconcile, release
from catalog import channels, revisions
from orders import intake, status, store, withdrawal
from recorder import handover
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

    def submit_handover(self, package: dict) -> str:
        """Accept, replay or reject a handover package without moving the device."""
        return handover.submit(self.business, self.endpoint.recording,
                               self.endpoint.position, package)

    def read_receipt_source(self, receipt_id: str) -> dict | None:
        receipts.read_receipt(self.business, receipt_id)
        source = handover.read_receipt_source(self.business, receipt_id)
        return None if source is None else dict(source)

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
            handover.initialize_handover_store(db)
        endpoint = RecordingEndpoint(root / 'device.sqlite3', recording)
        with db:
            db.execute('BEGIN IMMEDIATE')
            handover.reconcile_device_bindings(db, endpoint.package_bindings())
    except BaseException:
        db.close()
        raise
    return CaptureSession(db, endpoint)


def _check_order_can_advance(session: CaptureSession, event: RecordingEvent) -> None:
    db = session.business
    order = store.load_order(db, event.order_id)
    if event.kind == 'trigger':
        if ledger.find_receipt_for_order(db, event.order_id) is not None:
            raise InvalidRecordingEvent(f'order {event.order_id!r} is already settled')
    elif event.kind == 'withdraw':
        if not order.withdrawal_requested and ledger.find_receipt_for_order(db, event.order_id) is None:
            raise InvalidRecordingEvent(f'order {event.order_id!r} has no withdrawal request')


def _heal_mirror_if_behind(session: CaptureSession) -> None:
    """Mirror device-side bindings when an earlier business commit was lost.

    Callers must hold the business write transaction.  Replaying is
    idempotent and the device binding is authoritative for a bound position.
    """
    handover.reconcile_device_bindings(
        session.business, session.endpoint.package_bindings())


def _advance_device(session: CaptureSession, event_id: str) -> status.OrderStatus:
    db = session.business
    with db:
        # The business write transaction is held across the device commit:
        # competing submissions and advances therefore arbitrate by the order
        # in which they actually commit, and a failed device action rolls the
        # dispatch intent and mirror back with it.  The device writes its own
        # action, position and package binding in one device transaction.
        db.execute('BEGIN IMMEDIATE')
        _heal_mirror_if_behind(session)
        next_position = session.endpoint.position() + 1
        resolved = handover.resolve_next_event(
            db, session.endpoint.recording, next_position, event_id)
        event = resolved.event
        # Re-validate the device position and guarded device rules before any
        # write; an error leaves the device and package binding untouched.
        session.endpoint.check_resolved(resolved)
        _check_order_can_advance(session, event)
        if event.kind == 'trigger':
            progress.record_dispatch(
                db, event.order_id, store.load_order(db, event.order_id).intent.correlation)
        try:
            reply = session.endpoint.advance_resolved(resolved)
        except ReplyLost:
            reply = None
        reconcile.record_reply_in_transaction(db, event.order_id, reply)
        handover.record_committed_event(db, resolved)
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
    if order_ids:
        with session.business:
            session.business.execute('BEGIN IMMEDIATE')
            _heal_mirror_if_behind(session)
    for pending_id in order_ids:
        reconcile.reconcile_attempt(session.business, session.endpoint, pending_id,
                                    fail_publish=fail_publish)
        release.resume_release(session.business, session.endpoint, pending_id,
                               lose_reply=lose_release_reply)
    return tuple(session.read_order(pending_id) for pending_id in order_ids)
