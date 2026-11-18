from dataclasses import dataclass


@dataclass(frozen=True)
class Calibration:
    scale: int
    offset: int

    def __post_init__(self) -> None:
        if type(self.scale) is not int or type(self.offset) is not int:
            raise ValueError('calibration requires integer scale and offset')


def scale_micro_units(raw_value: int, calibration: Calibration) -> int:
    if type(raw_value) is not int:
        raise ValueError('a raw measurement must be an integer')
    return raw_value * calibration.scale + calibration.offset
