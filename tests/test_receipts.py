from archive.receipts import ReceiptNotFound
from capture.session import open_recording_session
from catalog.revisions import load_channel_revision
from recorder.events import InvalidRecordingEvent
from tests.support import RecordingCase, measure, revision, trigger


TWO_ORDERS = (trigger('A'), measure('A', 17), trigger('B'), measure('B', 17))


class ReceiptTests(RecordingCase):
    def publish_a(self, recording=TWO_ORDERS):
        session = self.open(recording)
        first = self.configuration()
        session.submit_order('A', first)
        self.advance('A-start', 'A-read')
        session.advance_recording(order_id='A')
        return session, first, self.receipt('A')

    def test_reopen_after_calibration_change(self):
        session, first, old = self.publish_a()
        night = self.configuration(name='R2', scale=3, offset=0, upper=50)
        session = self.reopen()
        self.assertEqual(session.read_receipt(old.receipt_id), old)
        self.assertEqual((old.configuration_id, old.raw_value, old.micro_units, old.quality),
                         (first, 17, 35, 'in_range'))
        session.submit_order('B', night)
        self.advance('B-start', 'B-read')
        session.advance_recording(order_id='B')
        new = self.receipt('B')
        self.assertEqual((new.configuration_id, new.raw_value, new.micro_units, new.quality),
                         (night, 17, 51, 'out_of_range'))
        self.assertEqual(session.read_receipt(old.receipt_id), old)
        self.assertEqual(self.counts(), (2, 2))

    def test_initialization_preserves_revision(self):
        session, first, old = self.publish_a()
        manual_revision = revision(name='manual', scale=4, offset=-2, upper=90)
        manual_id = session.add_channel_revision(manual_revision)
        session.submit_order('B', manual_id)
        self.advance('B-start')
        saved = session.endpoint.lookup_attempt('B')
        session = self.reopen()
        self.assertEqual(session.add_channel_revision(revision()), first)
        self.assertEqual(load_channel_revision(session.business, manual_id), manual_revision)
        self.assertEqual(session.read_receipt(old.receipt_id), old)
        self.assertEqual(session.read_order('B').configuration_id, manual_id)
        self.assertEqual(session.endpoint.position(), 3)
        self.assertEqual(session.endpoint.slot_owner(3), saved.attempt_id)
        with self.assertRaises(InvalidRecordingEvent):
            open_recording_session(self.workspace, TWO_ORDERS[:3])
        self.assertEqual(session.endpoint.position(), 3)
        self.advance('B-read')
        session.advance_recording(order_id='B')
        self.assertEqual(self.receipt('B').micro_units, 66)
        self.assertEqual(self.counts(), (2, 2))

    def test_receipt_lookup_read_only(self):
        session, _, old = self.publish_a()
        self.configuration(name='R2', scale=3, offset=0, upper=50)
        session = self.reopen()
        session.business.execute('PRAGMA query_only = ON')
        before = session.business.total_changes
        self.assertEqual(session.read_receipt(old.receipt_id), old)
        self.assertEqual(session.read_order('A').receipt_id, old.receipt_id)
        with self.assertRaises(ReceiptNotFound):
            session.read_receipt('receipt:missing')
        self.assertEqual(session.business.total_changes, before)
        self.assertEqual(self.counts(), (1, 1))
