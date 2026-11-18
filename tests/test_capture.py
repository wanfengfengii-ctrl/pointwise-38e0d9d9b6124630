from capture.progress import load_progress
from catalog.calibration import Calibration
from catalog.revisions import RevisionConflict
from recorder.events import InvalidRecordingEvent, RecordingEvent
from tests.support import BASIC, RecordingCase, measure, revision, trigger


class CaptureTests(RecordingCase):
    def test_sample_uses_fixed_calibration(self):
        session = self.open()
        fixed = self.configuration()
        self.assertEqual(session.submit_order('A', fixed).state, 'pending')
        self.configuration(name='R2', scale=3, offset=0, upper=50)
        self.advance('A-start', 'A-read')
        final, = session.advance_recording()
        receipt = self.receipt('A')
        self.assertEqual(final.state, 'measured')
        self.assertEqual((receipt.raw_value, receipt.micro_units, receipt.quality), (17, 35, 'in_range'))
        self.assertEqual((receipt.configuration_id, receipt.slot), (fixed, 3))
        self.assertEqual((receipt.measurement_event_id, receipt.measurement_event_position), ('A-read', 2))
        self.assertEqual(receipt.attempt_id, session.endpoint.lookup_attempt('A').attempt_id)
        self.assertIsNone(session.endpoint.slot_owner(3))
        self.assertEqual(self.counts(), (1, 1))

    def test_revision_conflict(self):
        session = self.open()
        saved = self.configuration()
        self.assertEqual(self.configuration(), saved)
        with self.assertRaises(RevisionConflict):
            self.configuration(scale=3)
        session.submit_order('A', saved)
        self.advance('A-start', 'A-read')
        session.advance_recording()
        self.assertEqual(self.receipt('A').micro_units, 35)

    def test_invalid_interval(self):
        session = self.open()
        with self.assertRaisesRegex(ValueError, 'lower limit exceeds'):
            session.add_channel_revision(revision(lower=36, upper=35))
        with self.assertRaises(ValueError):
            Calibration(True, 1)
        with self.assertRaises(ValueError):
            revision(lower=0, upper=35.0)
        self.assertEqual(session.business.execute('SELECT count(*) FROM channel_revisions').fetchone()[0], 0)
        self.assertEqual(self.counts(), (0, 0))

    def test_quality_bounds(self):
        cases = [('low', -1, 'out_of_range'), ('left', 0, 'in_range'),
                 ('right', 35, 'in_range'), ('high', 36, 'out_of_range')]
        recording = [event for order, raw, _ in cases for event in (trigger(order), measure(order, raw))]
        session = self.open(recording)
        fixed = self.configuration(scale=1, offset=0)
        for order, raw, quality in cases:
            with self.subTest(order=order):
                session.submit_order(order, fixed)
                self.advance(f'{order}-start', f'{order}-read')
                session.advance_recording(order_id=order)
                receipt = self.receipt(order)
                self.assertEqual((receipt.micro_units, receipt.quality), (raw, quality))
        self.assertEqual(self.counts(), (4, 4))

    def test_zero_is_measured_value(self):
        session = self.open((trigger('A'), measure('A', 0)))
        session.submit_order('A', self.configuration(scale=1, offset=0, upper=0))
        self.advance('A-start', 'A-read')
        attempt = session.endpoint.lookup_attempt('A')
        self.assertEqual(session.endpoint.read_terminal(attempt.attempt_id).raw_value, 0)
        session.advance_recording()
        receipt = self.receipt('A')
        self.assertEqual((receipt.outcome, receipt.raw_value, receipt.micro_units, receipt.quality),
                         ('measured', 0, 0, 'in_range'))
        self.assertEqual(receipt.measurement_event_id, 'A-read')

    def test_lost_trigger_reply(self):
        session = self.open((trigger('A', lose_reply=True), BASIC[1]))
        fixed = self.configuration()
        session.submit_order('A', fixed)
        pending, = session.advance_recording('A-start')
        self.assertEqual(pending.state, 'pending_review')
        self.assertIsNone(load_progress(session.business, 'A').attempt_id)
        original = session.endpoint.lookup_attempt('A')
        session = self.reopen()
        self.assertEqual(session.endpoint.position(), 1)
        pending, = session.advance_recording(order_id='A')
        self.assertIsNone(pending.receipt_id)
        self.assertEqual(load_progress(session.business, 'A').attempt_id, original.attempt_id)
        session.submit_order('A', fixed)
        self.advance('A-read')
        session.advance_recording()
        self.assertEqual(self.receipt('A').attempt_id, original.attempt_id)
        self.assertEqual(self.counts(), (1, 1))

    def test_invalid_recorded_event(self):
        session = self.open((trigger('A'), trigger('B')))
        fixed = self.configuration()
        session.submit_order('A', fixed)
        session.submit_order('B', fixed)
        for event in ('missing', 'B-start'):
            with self.assertRaises(InvalidRecordingEvent):
                session.advance_recording(event)
        self.assertEqual(session.endpoint.position(), 0)
        self.assertIsNone(load_progress(session.business, 'A'))
        with self.assertRaises(InvalidRecordingEvent):
            RecordingEvent('bad', 'measure', 'A', raw_value=True)
        self.advance('A-start')
        owner = session.endpoint.slot_owner(3)
        with self.assertRaisesRegex(InvalidRecordingEvent, 'occupied'):
            session.advance_recording('B-start')
        self.assertEqual(session.endpoint.position(), 1)
        self.assertEqual(session.endpoint.slot_owner(3), owner)
        self.assertIsNone(load_progress(session.business, 'B'))
        self.assertEqual(self.counts(), (1, 0))

    def test_wide_integer_survives_both_stores(self):
        raw = 1 << 100
        scale = -(1 << 80)
        expected = -(1 << 180) + 3
        session = self.open((trigger('A'), measure('A', raw)))
        session.submit_order('A', self.configuration(scale=scale, offset=3, lower=expected, upper=expected))
        self.advance('A-start', 'A-read')
        session = self.reopen()
        attempt = session.endpoint.lookup_attempt('A')
        self.assertEqual(session.endpoint.read_terminal(attempt.attempt_id).raw_value, raw)
        session.advance_recording()
        self.reopen()
        receipt = self.receipt('A')
        self.assertEqual((receipt.raw_value, receipt.micro_units, receipt.quality), (raw, expected, 'in_range'))
