from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceTerminal:
    attempt_id: str
    slot: int
    kind: str
    raw_value: int | None
    event_id: str
    event_position: int


@dataclass(frozen=True)
class DeviceReply:
    correlation: str
    attempt_id: str
    terminal: DeviceTerminal | None


class ReplyLost(Exception):
    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f'device committed {operation} but its reply was lost')


@dataclass(frozen=True)
class ReleaseReply:
    attempt_id: str
    already_released: bool
