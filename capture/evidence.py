from dataclasses import dataclass

from recorder.replies import DeviceTerminal


class InvalidTerminalEvidence(ValueError):
    pass


@dataclass(frozen=True)
class TerminalEvidence:
    order_id: str
    terminal: DeviceTerminal
    package_id: str | None = None
    sha256: str | None = None
    package_position: int | None = None

    @property
    def source(self) -> dict | None:
        if self.package_id is None:
            return None
        return {'package_id': self.package_id, 'sha256': self.sha256,
                'package_position': self.package_position}


def bind_terminal_evidence(
    order_id: str, attempt_id: str | None, terminal: DeviceTerminal,
    source: dict | None = None,
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
    if source is None:
        return TerminalEvidence(order_id, terminal)
    if (not isinstance(source.get('package_id'), str) or not source['package_id'].strip()
            or not isinstance(source.get('sha256'), str) or not source['sha256']
            or type(source.get('package_position')) is not int
            or source['package_position'] < 1):
        raise InvalidTerminalEvidence('package source requires an identity and position')
    return TerminalEvidence(order_id, terminal, source['package_id'], source['sha256'],
                            source['package_position'])
