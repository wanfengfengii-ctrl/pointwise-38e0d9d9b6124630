from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class ChannelKey:
    station: str
    channel: str

    def __post_init__(self) -> None:
        for field in (self.station, self.channel):
            if not isinstance(field, str) or not field.strip():
                raise ValueError('station and channel must be nonempty strings')
            field.encode('utf-8')


def initialize_channel_store(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            station TEXT NOT NULL,
            channel TEXT NOT NULL,
            PRIMARY KEY (station, channel)
        )
    ''')


def ensure_channel(db: Connection, key: ChannelKey) -> None:
    db.execute('''
        INSERT INTO channels (station, channel) VALUES (?, ?)
        ON CONFLICT (station, channel) DO NOTHING
    ''', (key.station, key.channel))
