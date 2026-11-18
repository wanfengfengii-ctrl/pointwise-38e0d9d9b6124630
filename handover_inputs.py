"""Handover packages and independent expected digests for the sample."""
import hashlib
import json
from pathlib import Path

from recorder.events import RecordingEvent, load_recording


ROOT_RECORDING = load_recording(Path(__file__).resolve().parent / 'recordings' / 'basic.json')[:1]
ROOT_SHA256 = '8dd7f21ad9e76d805f7b800a33c31999e6d395fbf7fdaab781d17a855d0f0a5c'
U2_SHA256 = '802b5f4e90849ce1e0e7d2f6904809049a9b695f2a853b0f04671a0a051fd793'

U2 = {
    'package_id': 'U2', 'predecessor_id': 'ROOT', 'predecessor_sha256': ROOT_SHA256,
    'events': [
        {'event_id': 'A-read', 'kind': 'measure', 'order_id': 'A', 'raw_value': 17, 'lose_reply': True},
        {'event_id': 'A-repeat', 'kind': 'measure', 'order_id': 'A', 'raw_value': 23},
    ],
}
U3 = {
    'package_id': 'U3', 'predecessor_id': 'U2', 'predecessor_sha256': U2_SHA256,
    'events': [{'event_id': 'A-confirm', 'kind': 'measure', 'order_id': 'A', 'raw_value': 99}],
}
ALT = {
    'package_id': 'ALT', 'predecessor_id': 'ROOT', 'predecessor_sha256': ROOT_SHA256,
    'events': [{'event_id': 'A-read', 'kind': 'measure', 'order_id': 'A', 'raw_value': 99}],
}
STALE = {
    'package_id': 'STALE', 'predecessor_id': 'U2', 'predecessor_sha256': '0' * 64,
    'events': [{'event_id': 'A-confirm', 'kind': 'measure', 'order_id': 'A', 'raw_value': 99}],
}
EXPECTED_SHA256 = {
    'ROOT': ROOT_SHA256,
    'U2': U2_SHA256,
    'U3': '240f0944d1eb961e7a15fd8cae5836d0c6f910121a8b0452096d3b86e93d58e6',
    'ALT': 'c51ec2537bcb737dff179870c651b59fc989a28d60210e81427bcf84d023e385',
    'STALE': 'b7b441adafacf85d2f3dac75c79ec54ba7da426f5a5323304db36568eb847f11',
}
EXPECTED_A_SOURCE = {'package_id': 'U2', 'sha256': U2_SHA256, 'package_position': 1}


def _sample_digest(package_id, predecessor_id, predecessor_sha256, events):
    rows = [[e.event_id, e.kind, e.order_id,
             None if e.slot is None else hex(e.slot),
             None if e.raw_value is None else hex(e.raw_value), e.lose_reply]
            for e in events]
    canonical = json.dumps([package_id, predecessor_id, predecessor_sha256, rows],
                           ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return hashlib.sha256(canonical).hexdigest()


def check_inputs():
    observed = {'ROOT': _sample_digest('ROOT', None, None, ROOT_RECORDING)}
    for package in (U2, U3, ALT, STALE):
        name = package['package_id']
        events = tuple(RecordingEvent(**row) for row in package['events'])
        observed[name] = _sample_digest(name, package['predecessor_id'], package['predecessor_sha256'], events)
    if observed != EXPECTED_SHA256:
        raise AssertionError(f'sample digests differ: {observed!r}')
    for package in (U2, U3, ALT, STALE):
        name = package['package_id']
        matches = package['predecessor_sha256'] == observed[package['predecessor_id']]
        if matches != (package is not STALE):
            raise AssertionError(f'unexpected sample link for {name}')
    print('HANDOVER_INPUTS ' + json.dumps(observed, sort_keys=True))


if __name__ == '__main__':
    check_inputs()
