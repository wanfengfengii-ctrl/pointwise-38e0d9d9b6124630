from dataclasses import dataclass
from sqlite3 import Connection

from orders.store import UnknownOrder


@dataclass(frozen=True)
class OrderStatus:
    order_id: str
    configuration_id: str
    state: str
    withdrawal_requested: bool
    receipt_id: str | None


def read_order(db: Connection, order_id: str) -> OrderStatus:
    # A single statement sees intent, progress and published settlement together.
    row = db.execute('''
        SELECT orders.configuration_id, orders.withdrawal_requested,
               progress.phase, receipts.outcome, receipts.receipt_id
        FROM sampling_orders AS orders
        LEFT JOIN capture_progress AS progress ON progress.order_id = orders.order_id
        LEFT JOIN receipts ON receipts.order_id = orders.order_id
        WHERE orders.order_id = ?
    ''', (order_id,)).fetchone()
    if row is None:
        raise UnknownOrder(order_id)
    configuration_id, requested, phase, outcome, receipt_id = row
    if outcome is not None:
        state = outcome
    elif requested or phase in {'dispatching', 'reconciling', 'publishing'}:
        state = 'pending_review'
    else:
        state = 'pending'
    return OrderStatus(order_id, configuration_id, state, bool(requested), receipt_id)
