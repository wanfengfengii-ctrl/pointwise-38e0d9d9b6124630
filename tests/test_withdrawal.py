from dataclasses import replace

from archive.settle import InvalidSettlement, settle_terminal
from capture.evidence import TerminalEvidence
from capture.progress import CaptureBindingConflict, load_progress
from capture.reconcile import record_device_reply
from orders.store import UnknownOrder
from recorder.events import InvalidRecordingEvent, RecordingEvent
from recorder.replies import DeviceReply, DeviceTerminal
from tests.support import HANDOVER, RecordingCase, measure, trigger


MARKER = 'SAMPLE_CUSTODY_MISMATCH'
CANCEL_FIRST = (trigger('C'), RecordingEvent('C-withdraw', 'withdraw', 'C'), measure('C', 11))


class WithdrawalTests(RecordingCase):
    def prepare_c(self):
        session = self.open(CANCEL_FIRST)
        session.submit_order('C', self.configuration())
        self.advance('C-start')
        return session

    def test_withdrawal_before_read(self):
        session = self.prepare_c()
        pending = session.request_withdrawal('C')
        self.assertEqual(pending.state, 'pending_review')
        self.assertIsNone(pending.receipt_id)
        self.advance('C-withdraw')
        session.advance_recording(order_id='C')
        original = self.receipt('C')
        self.advance('C-read')
        session.advance_recording(order_id='C')
        receipt = self.receipt('C')
        self.assertEqual(receipt, original)
        self.assertEqual(receipt.outcome, 'withdrawn')
        self.assertEqual((receipt.raw_value, receipt.micro_units, receipt.quality,
                          receipt.measurement_event_id, receipt.measurement_event_position), (None,) * 5)
        self.assertEqual((receipt.terminal_event_id, receipt.terminal_event_position), ('C-withdraw', 2))
        self.assertEqual(self.counts(), (1, 1))

    def test_late_measurement_while_held(self):
        session = self.prepare_c()
        session.request_withdrawal('C')
        self.advance('C-withdraw')
        attempt = session.endpoint.lookup_attempt('C')
        terminal = session.endpoint.read_terminal(attempt.attempt_id)
        self.assertEqual((terminal.kind, terminal.raw_value), ('withdrawn', None))
        self.assertIsNone(session.read_order('C').receipt_id)
        self.advance('C-read')
        self.assertEqual(session.endpoint.read_terminal(attempt.attempt_id), terminal)
        session.advance_recording(order_id='C')
        receipt = self.receipt('C')
        self.assertEqual((receipt.outcome, receipt.terminal_event_id), ('withdrawn', 'C-withdraw'))
        self.assertIsNone(receipt.raw_value)
        self.assertEqual(self.counts(), (1, 1))

    def test_repeat_withdrawal(self):
        session = self.prepare_c()
        with self.assertRaisesRegex(InvalidRecordingEvent, 'no withdrawal request'):
            session.advance_recording('C-withdraw')
        self.assertEqual(session.endpoint.position(), 1)
        pending = session.request_withdrawal('C')
        self.assertEqual(session.request_withdrawal('C'), pending)
        self.assertEqual(session.endpoint.position(), 1)
        self.advance('C-withdraw')
        final, = session.advance_recording(order_id='C')
        self.assertEqual(session.request_withdrawal('C'), final)
        self.assertEqual(self.counts(), (1, 1))

    def test_unknown_order(self):
        session = self.open(CANCEL_FIRST)
        with self.assertRaises(UnknownOrder):
            session.request_withdrawal('C')
        with self.assertRaises(UnknownOrder):
            session.advance_recording('C-start')
        self.assertEqual(session.endpoint.position(), 0)
        self.assertEqual(self.counts(), (0, 0))

    def test_read_before_lost_withdrawal_reply(self):
        session = self.open(HANDOVER[:3])
        session.submit_order('A', self.configuration())
        self.advance('A-start', 'A-read')
        session.request_withdrawal('A')
        pending, = session.advance_recording('A-withdraw')
        self.assertEqual((pending.state, pending.receipt_id), ('pending_review', None), MARKER)
        attempt = session.endpoint.lookup_attempt('A')
        actual = session.endpoint.read_terminal(attempt.attempt_id)
        self.assertEqual((actual.kind, actual.raw_value, actual.event_id), ('measured', 17, 'A-read'), MARKER)
        session = self.reopen()
        session.advance_recording()
        receipt = self.receipt('A', marker=MARKER)
        self.assertEqual((receipt.outcome, receipt.raw_value, receipt.measurement_event_id),
                         ('measured', 17, 'A-read'), MARKER)
        self.assertEqual(self.counts(), (1, 1), MARKER)

    def test_unknown_keeps_pending(self):
        session = self.prepare_c()
        session.request_withdrawal('C')
        recovered, = session.advance_recording(order_id='C')
        self.assertEqual((recovered.state, recovered.receipt_id), ('pending_review', None))
        self.assertEqual(load_progress(session.business, 'C').phase, 'reconciling')
        self.assertEqual(self.counts(), (1, 0))
        self.advance('C-withdraw')
        session.advance_recording(order_id='C')
        self.assertEqual(self.receipt('C').outcome, 'withdrawn')

    def test_independent_measured_terminal(self):
        session = self.open((trigger('A'),))
        session.submit_order('A', self.configuration())
        self.advance('A-start')
        session.request_withdrawal('A')
        attempt = session.endpoint.lookup_attempt('A')
        terminal = DeviceTerminal(attempt.attempt_id, 3, 'measured', 17, 'fixture-measurement', 7)
        receipt = settle_terminal(session.business, TerminalEvidence('A', terminal))
        self.assertEqual((receipt.outcome, receipt.raw_value, receipt.micro_units), ('measured', 17, 35), MARKER)
        self.assertEqual((receipt.measurement_event_id, receipt.measurement_event_position),
                         ('fixture-measurement', 7), MARKER)
        self.assertEqual(receipt, self.receipt('A', marker=MARKER))
        self.assertEqual(self.counts(), (1, 1), MARKER)

    def test_rejects_mismatched_or_incomplete_evidence(self):
        session = self.open((trigger('A'),))
        session.submit_order('A', self.configuration())
        self.advance('A-start')
        binding = load_progress(session.business, 'A')
        with self.assertRaises(CaptureBindingConflict):
            record_device_reply(session.business, 'A', DeviceReply('other', binding.attempt_id, None))
        terminal = DeviceTerminal(binding.attempt_id, 3, 'measured', 17, 'source', 2)
        for invalid in (replace(terminal, attempt_id='other'), replace(terminal, raw_value=None),
                        replace(terminal, event_id=''), replace(terminal, event_position=0),
                        replace(terminal, kind='withdrawn')):
            with self.subTest(terminal=invalid), self.assertRaises(InvalidSettlement):
                settle_terminal(session.business, TerminalEvidence('A', invalid))
        self.assertEqual(load_progress(session.business, 'A'), binding)
        self.assertIsNone(session.read_order('A').receipt_id)
        self.assertEqual(self.counts(), (1, 0))
