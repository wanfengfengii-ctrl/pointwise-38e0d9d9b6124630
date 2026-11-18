from dataclasses import dataclass

from recorder.replies import DeviceTerminal


class InvalidTerminalEvidence(ValueError):
    pass


@dataclass(frozen=True)
class TerminalEvidence:
    order_id: str
    terminal: DeviceTerminal


def bind_terminal_evidence(
    order_id: str, attempt_id: str | None, terminal: DeviceTerminal,
) -> TerminalEvidence:
    if not isinstance(terminal, DeviceTerminal) or not attempt_id or terminal.attempt_id != attempt_id:
        raise InvalidTerminalEvidence(f'device terminal identity does not match {order_id!r}')
    if (type(terminal.slot) is not int or terminal.slot < 0
            or not isinstance(terminal.event_id, str) or not terminal.event_id.strip()
            or type(terminal.event_position) is not int or terminal.event_position < 1):
        raise InvalidTerminalEvidence('terminal evidence requires a slot and recording source')
    if terminal.kind == 'measured':
        valid = type(terminal.raw_value) is int
    elif terminal.kind == 'withdrawn':
        valid = terminal.raw_value is None
    else:
        valid = False
    if not valid:
        raise InvalidTerminalEvidence('terminal kind and raw payload do not agree')
    return TerminalEvidence(order_id, terminal)
