from sqlite3 import Connection


def initialize_binding_store(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS recording_package_bindings (
            position INTEGER PRIMARY KEY NOT NULL CHECK (position > 0),
            package_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            package_position INTEGER NOT NULL CHECK (package_position > 0),
            event_id TEXT NOT NULL,
            chain_start INTEGER CHECK (chain_start > 0),
            chain_count INTEGER CHECK (chain_count > 0),
            CHECK ((chain_start IS NULL AND chain_count IS NULL)
                OR (chain_start IS NOT NULL AND chain_count IS NOT NULL
                    AND chain_start = position))
        )
    ''')


def record(db: Connection, position: int, package_id: str, sha256: str,
           package_position: int, event_id: str, *,
           chain_count: int | None = None) -> None:
    """Bind a package event in the same device transaction as its action."""
    db.execute('''
        INSERT INTO recording_package_bindings (
            position, package_id, sha256, package_position, event_id, chain_start, chain_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (position, package_id, sha256, package_position, event_id,
          position if chain_count is not None else None, chain_count))


def load_all(db: Connection) -> tuple[tuple, ...]:
    return tuple(db.execute('''
        SELECT position, package_id, sha256, package_position, event_id,
               chain_start, chain_count
        FROM recording_package_bindings ORDER BY position
    '''))
