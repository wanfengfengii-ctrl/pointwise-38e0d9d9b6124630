from dataclasses import dataclass


def validate_limits(lower: int, upper: int) -> None:
    if type(lower) is not int or type(upper) is not int:
        raise ValueError('quality limits must be integers in micro-units')
    if lower > upper:
        raise ValueError('quality lower limit exceeds upper limit')


@dataclass(frozen=True)
class QualityLimits:
    lower: int
    upper: int

    def __post_init__(self) -> None:
        validate_limits(self.lower, self.upper)


def contains_micro_units(value: int, limits: QualityLimits) -> bool:
    return limits.lower <= value <= limits.upper
