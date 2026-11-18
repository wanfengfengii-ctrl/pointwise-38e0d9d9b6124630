from sqlite3 import Connection

from catalog.revisions import load_channel_revision
from orders.identity import OrderConflict, OrderIntent, same_submission
from orders.status import OrderStatus, read_order
from orders.store import UnknownOrder, insert_order, load_order


def submit_order(db: Connection, order_id: str, configuration_id: str) -> OrderStatus:
    intent = OrderIntent(order_id, configuration_id)
    with db:
        db.execute('BEGIN IMMEDIATE')
        try:
            original = load_order(db, order_id)
        except UnknownOrder:
            load_channel_revision(db, configuration_id)
            insert_order(db, intent)
        else:
            if not same_submission(original.intent, intent):
                raise OrderConflict(order_id)
        return read_order(db, order_id)
