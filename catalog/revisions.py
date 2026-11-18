import json
from dataclasses import dataclass
from sqlite3 import Connection

from catalog.calibration import Calibration
from catalog.channels import ChannelKey, ensure_channel
from catalog.limits import QualityLimits


class RevisionConflict(ValueError):
    pass


class UnknownConfiguration(LookupError):
    pass


@dataclass(frozen=True)
class ChannelRevision:
    channel: ChannelKey
    name: str
    calibration: Calibration
    limits: QualityLimits

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('revision name must be a nonempty string')
        self.name.encode('utf-8')
        if not isinstance(self.channel, ChannelKey):
            raise ValueError('revision requires a channel key')
        if not isinstance(self.calibration, Calibration) or not isinstance(self.limits, QualityLimits):
            raise ValueError('revision requires a calibration and quality limits')

    @property
    def configuration_id(self) -> str:
        return json.dumps(
            [self.channel.station, self.channel.channel, self.name],
            separators=(',', ':'),
        )


def initialize_revision_store(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS channel_revisions (
            configuration_id TEXT PRIMARY KEY NOT NULL,
            station TEXT NOT NULL,
            channel TEXT NOT NULL,
            revision_name TEXT NOT NULL,
            scale TEXT NOT NULL,
            offset TEXT NOT NULL,
            lower_limit TEXT NOT NULL,
            upper_limit TEXT NOT NULL,
            UNIQUE (station, channel, revision_name),
            FOREIGN KEY (station, channel) REFERENCES channels (station, channel)
        )
    ''')


def _find_revision(db: Connection, configuration_id: str) -> ChannelRevision | None:
    row = db.execute('''
        SELECT station, channel, revision_name, scale, offset, lower_limit, upper_limit
        FROM channel_revisions WHERE configuration_id = ?
    ''', (configuration_id,)).fetchone()
    if row is None:
        return None
    station, channel, name, scale, offset, lower, upper = row
    return ChannelRevision(
        ChannelKey(station, channel), name,
        Calibration(int(scale, 16), int(offset, 16)),
        QualityLimits(int(lower, 16), int(upper, 16)),
    )


def add_channel_revision(db: Connection, revision: ChannelRevision) -> str:
    configuration_id = revision.configuration_id
    with db:
        db.execute('BEGIN IMMEDIATE')
        current = _find_revision(db, configuration_id)
        if current is not None:
            if current != revision:
                raise RevisionConflict(configuration_id)
            return configuration_id
        ensure_channel(db, revision.channel)
        db.execute('''
            INSERT INTO channel_revisions (
                configuration_id, station, channel, revision_name,
                scale, offset, lower_limit, upper_limit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            configuration_id, revision.channel.station, revision.channel.channel,
            revision.name, hex(revision.calibration.scale), hex(revision.calibration.offset),
            hex(revision.limits.lower), hex(revision.limits.upper),
        ))
    return configuration_id


def load_channel_revision(db: Connection, configuration_id: str) -> ChannelRevision:
    revision = _find_revision(db, configuration_id)
    if revision is None:
        raise UnknownConfiguration(configuration_id)
    return revision
