from sqlite3 import Connection

from archive.ledger import ReceiptRecord
from archive.publication import commit_settlement
from archive.quality import process_sample
from capture.evidence import InvalidTerminalEvidence, TerminalEvidence, bind_terminal_evidence
from capture.progress import load_progress
from catalog.revisions import load_channel_revision
from orders.store import load_order


class InvalidSettlement(ValueError):
    pass


def settle_terminal(db: Connection, evidence: TerminalEvidence, *, fail_commit: bool = False) -> ReceiptRecord:
    order = load_order(db, evidence.order_id)
    binding = load_progress(db, evidence.order_id)
    if binding is None:
        raise InvalidSettlement(f'order {evidence.order_id!r} has no device binding')
    try:
        terminal = bind_terminal_evidence(evidence.order_id, binding.attempt_id, evidence.terminal).terminal
    except InvalidTerminalEvidence as error:
        raise InvalidSettlement(str(error)) from error

    configuration_id = order.intent.configuration_id
    revision = load_channel_revision(db, configuration_id)
    sample = process_sample(terminal.raw_value, revision) if terminal.kind == 'measured' else None
    record = ReceiptRecord(
        receipt_id=f'receipt:{order.intent.order_id}',
        order_id=order.intent.order_id,
        configuration_id=configuration_id,
        attempt_id=terminal.attempt_id,
        slot=terminal.slot,
        outcome=terminal.kind,
        raw_value=None if sample is None else sample.raw_value,
        micro_units=None if sample is None else sample.micro_units,
        quality=None if sample is None else sample.quality,
        terminal_event_id=terminal.event_id,
        terminal_event_position=terminal.event_position,
    )
    return commit_settlement(db, record, fail_commit=fail_commit, source=evidence.source)
