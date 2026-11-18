from sqlite3 import Connection

from orders.status import OrderStatus, read_order
from orders.store import set_withdrawal_intent


def request_withdrawal(db: Connection, order_id: str) -> OrderStatus:
    with db:
        db.execute('BEGIN IMMEDIATE')
        status = read_order(db, order_id)
        if status.receipt_id is None and not status.withdrawal_requested:
            set_withdrawal_intent(db, order_id)
            return read_order(db, order_id)
        return status
