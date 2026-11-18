from dataclasses import dataclass

from catalog.calibration import scale_micro_units
from catalog.limits import contains_micro_units
from catalog.revisions import ChannelRevision


@dataclass(frozen=True)
class ProcessedSample:
    raw_value: int
    micro_units: int
    quality: str


def process_sample(raw_value: int, revision: ChannelRevision) -> ProcessedSample:
    micro_units = scale_micro_units(raw_value, revision.calibration)
    quality = 'in_range' if contains_micro_units(micro_units, revision.limits) else 'out_of_range'
    return ProcessedSample(raw_value, micro_units, quality)
