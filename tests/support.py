from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from capture.session import open_recording_session
from catalog.calibration import Calibration
from catalog.channels import ChannelKey
from catalog.limits import QualityLimits
from catalog.revisions import ChannelRevision
from recorder.events import RecordingEvent, load_recording


RECORDINGS = Path(__file__).resolve().parent.parent / 'recordings'
BASIC = load_recording(RECORDINGS / 'basic.json')
HANDOVER = load_recording(RECORDINGS / 'handover.json')


def revision(name='R1', scale=2, offset=1, lower=0, upper=35):
    return ChannelRevision(ChannelKey('north-bank', 'K1'), name,
                           Calibration(scale, offset), QualityLimits(lower, upper))


def trigger(order_id, slot=3, *, lose_reply=False):
    return RecordingEvent(f'{order_id}-start', 'trigger', order_id, slot=slot, lose_reply=lose_reply)


def measure(order_id, raw_value):
    return RecordingEvent(f'{order_id}-read', 'measure', order_id, raw_value=raw_value)


class RecordingCase(TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name)
        self.session = None
        self.addCleanup(self.close_session)

    def close_session(self):
        if self.session is not None:
            self.session.close()
            self.session = None

    def open(self, recording=BASIC):
        self.recording = tuple(recording)
        return self.reopen()

    def reopen(self):
        self.close_session()
        self.session = open_recording_session(self.workspace, self.recording)
        return self.session

    def configuration(self, **kwargs):
        return self.session.add_channel_revision(revision(**kwargs))

    def advance(self, *event_ids):
        for event_id in event_ids:
            self.session.advance_recording(event_id)

    def receipt(self, order_id, *, marker=None):
        status = self.session.read_order(order_id)
        self.assertIsNotNone(status.receipt_id, marker)
        return self.session.read_receipt(status.receipt_id)

    def counts(self):
        rows = self.session.business.execute('SELECT count(*) FROM receipts').fetchone()[0]
        return self.session.endpoint.trigger_count(), rows
