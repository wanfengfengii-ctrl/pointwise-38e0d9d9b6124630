from sqlite3 import Connection

from archive.ledger import ReceiptRecord, find_receipt_for_order
from archive.settle import settle_terminal
from capture.evidence import bind_terminal_evidence
from capture.progress import (
    CaptureBindingConflict, bind_attempt, load_progress, mark_publication_pending,
    mark_reconciliation_pending,
)
from recorder.endpoint import RecordingEndpoint
from recorder.replies import DeviceReply


def record_device_reply(db: Connection, order_id: str, reply: DeviceReply | None) -> None:
    with db:
        db.execute('BEGIN IMMEDIATE')
        progress = load_progress(db, order_id)
        if progress is None:
            raise CaptureBindingConflict(f'order {order_id!r} was never prepared for dispatch')
        if reply is None:
            mark_reconciliation_pending(db, order_id)
            return
        bind_attempt(db, order_id, reply.correlation, reply.attempt_id)
        if reply.terminal is not None:
            bind_terminal_evidence(order_id, reply.attempt_id, reply.terminal)
            mark_publication_pending(db, order_id)


def reconcile_attempt(
    db: Connection, endpoint: RecordingEndpoint, order_id: str, *, fail_publish: bool = False,
) -> ReceiptRecord | None:
    receipt = find_receipt_for_order(db, order_id)
    if receipt is not None:
        return receipt
    progress = load_progress(db, order_id)
    if progress is None or progress.phase == 'released':
        return None
    attempt_id = progress.attempt_id
    if attempt_id is None:
        original = endpoint.lookup_attempt(progress.correlation)
        if original is None:
            record_device_reply(db, order_id, None)
            return None
        record_device_reply(db, order_id, DeviceReply(original.correlation, original.attempt_id, None))
        attempt_id = original.attempt_id
    terminal = endpoint.read_terminal(attempt_id)
    if terminal is None:
        # Another session may already have published and released this identity.
        receipt = find_receipt_for_order(db, order_id)
        if receipt is None:
            record_device_reply(db, order_id, None)
        return receipt
    evidence = bind_terminal_evidence(order_id, attempt_id, terminal)
    with db:
        db.execute('BEGIN IMMEDIATE')
        mark_publication_pending(db, order_id)
    return settle_terminal(db, evidence, fail_commit=fail_publish)
