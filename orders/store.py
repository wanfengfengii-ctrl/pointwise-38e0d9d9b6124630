from dataclasses import dataclass
from sqlite3 import Connection

from orders.identity import OrderIntent


class UnknownOrder(LookupError):
    pass


@dataclass(frozen=True)
class StoredOrder:
    intent: OrderIntent
    withdrawal_requested: bool


def initialize_order_store(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS sampling_orders (
            order_id TEXT PRIMARY KEY NOT NULL,
            configuration_id TEXT NOT NULL REFERENCES channel_revisions (configuration_id),
            withdrawal_requested INTEGER NOT NULL DEFAULT 0
                CHECK (withdrawal_requested IN (0, 1))
        )
    ''')


def load_order(db: Connection, order_id: str) -> StoredOrder:
    row = db.execute('''
        SELECT configuration_id, withdrawal_requested FROM sampling_orders WHERE order_id = ?
    ''', (order_id,)).fetchone()
    if row is None:
        raise UnknownOrder(order_id)
    configuration_id, withdrawal_requested = row
    return StoredOrder(OrderIntent(order_id, configuration_id), bool(withdrawal_requested))


def insert_order(db: Connection, intent: OrderIntent) -> None:
    db.execute('''
        INSERT INTO sampling_orders (order_id, configuration_id) VALUES (?, ?)
    ''', (intent.order_id, intent.configuration_id))


def set_withdrawal_intent(db: Connection, order_id: str) -> None:
    db.execute('''
        UPDATE sampling_orders SET withdrawal_requested = 1
        WHERE order_id = ? AND withdrawal_requested = 0
    ''', (order_id,))
