import sqlite3
from dataclasses import replace

from archive.ledger import find_release_authorization
from archive.publication import SettlementConflict
from archive.receipts import ReceiptNotFound
from archive.settle import settle_terminal
from capture.evidence import TerminalEvidence
from capture.progress import load_progress
from capture.release import resume_release
from recorder.endpoint import ReleaseRejected
from tests.support import HANDOVER, RecordingCase, measure, trigger


MARKER = 'SAMPLE_CUSTODY_MISMATCH'
REUSE = (trigger('A'), measure('A', 17), trigger('B'), measure('B', 23))


class RecoveryTests(RecordingCase):
    def measured_a(self, recording=REUSE):
        session = self.open(recording)
        fixed = self.configuration()
        session.submit_order('A', fixed)
        self.advance('A-start', 'A-read')
        return session, fixed, session.endpoint.lookup_attempt('A').attempt_id

    def assert_commit_fails(self, session):
        with self.assertRaises(sqlite3.IntegrityError) as failure:
            session.advance_recording(order_id='A', fail_publish=True)
        self.assertEqual(failure.exception.sqlite_errorname, 'SQLITE_CONSTRAINT_FOREIGNKEY')

    def test_publish_failure_keeps_evidence(self):
        session, fixed, original_id = self.measured_a()
        before = session.endpoint.read_terminal(original_id)
        self.assert_commit_fails(session)
        self.assertFalse(session.business.in_transaction)
        self.assertIsNone(session.read_order('A').receipt_id, MARKER)
        with self.assertRaises(ReceiptNotFound):
            session.read_receipt('receipt:A')
        self.assertEqual(self.counts(), (1, 0), MARKER)
        self.assertEqual(session.endpoint.read_terminal(original_id), before, MARKER)
        session = self.reopen()
        self.assertEqual(session.endpoint.read_terminal(original_id), before, MARKER)
        self.assertEqual(session.submit_order('A', fixed).state, 'pending_review')
        session.advance_recording()
        receipt = self.receipt('A', marker=MARKER)
        self.assertEqual((receipt.attempt_id, receipt.raw_value), (original_id, 17), MARKER)
        self.assertIsNone(session.endpoint.read_terminal(original_id))
        self.assertTrue(session.endpoint.lookup_attempt('A').released)
        session.advance_recording(order_id='A')
        self.assertEqual(self.counts(), (1, 1))

    def test_no_release_before_commit(self):
        session, _, attempt_id = self.measured_a()
        before = session.endpoint.read_terminal(attempt_id)
        self.assertEqual(load_progress(session.business, 'A').phase, 'publishing')
        self.assertFalse(resume_release(session.business, session.endpoint, 'A'), MARKER)
        self.assert_commit_fails(session)
        self.assertIsNone(find_release_authorization(session.business, 'A', attempt_id), MARKER)
        self.assertFalse(resume_release(session.business, session.endpoint, 'A'), MARKER)
        self.assertEqual(session.endpoint.slot_owner(3), attempt_id, MARKER)
        self.assertEqual(session.endpoint.read_terminal(attempt_id), before, MARKER)
        self.assertIsNone(session.read_order('A').receipt_id, MARKER)
        session = self.reopen()
        session.advance_recording(order_id='A')
        self.assertEqual(self.receipt('A', marker=MARKER).raw_value, 17)
        self.assertEqual(self.counts(), (1, 1))

    def test_replay_after_publish(self):
        session, _, attempt_id = self.measured_a()
        evidence = TerminalEvidence('A', session.endpoint.read_terminal(attempt_id))
        session.advance_recording(order_id='A')
        initial = self.receipt('A')
        session = self.reopen()
        self.assertEqual(settle_terminal(session.business, evidence), initial)
        conflicting = replace(evidence, terminal=replace(evidence.terminal, raw_value=18))
        with self.assertRaises(SettlementConflict):
            settle_terminal(session.business, conflicting)
        self.assertEqual(self.receipt('A'), initial)
        self.assertEqual(self.counts(), (1, 1))

    def test_reused_slot_survives_release_retry(self):
        session, fixed, a_id = self.measured_a()
        session.advance_recording(order_id='A', lose_release_reply=True)
        initial = self.receipt('A', marker=MARKER)
        self.assertEqual(initial.raw_value, 17, MARKER)
        self.assertTrue(session.endpoint.lookup_attempt('A').released)
        self.assertNotEqual(load_progress(session.business, 'A').phase, 'released')
        self.assertIsNone(session.endpoint.slot_owner(3))
        session.submit_order('B', fixed)
        self.advance('B-start', 'B-read')
        b_id = session.endpoint.lookup_attempt('B').attempt_id
        self.assertNotEqual(a_id, b_id)
        saved_b = session.endpoint.read_terminal(b_id)
        self.assertEqual(saved_b.raw_value, 23, MARKER)
        self.assertIsNone(session.read_order('B').receipt_id)
        session = self.reopen()
        session.advance_recording(order_id='A')
        self.assertEqual(load_progress(session.business, 'A').phase, 'released')
        self.assertEqual(session.endpoint.slot_owner(3), b_id, MARKER)
        self.assertEqual(session.endpoint.read_terminal(b_id), saved_b, MARKER)
        self.assertEqual(self.receipt('A', marker=MARKER), initial, MARKER)
        session.advance_recording(order_id='B')
        self.assertEqual(self.receipt('B', marker=MARKER).raw_value, 23, MARKER)
        for order in ('A', 'B'):
            self.assertEqual(session.submit_order(order, fixed).receipt_id, self.receipt(order).receipt_id)
        self.assertEqual(self.counts(), (2, 2), MARKER)

    def test_old_release_retry(self):
        session, fixed, a_id = self.measured_a()
        session.advance_recording(order_id='A', lose_release_reply=True)
        session.submit_order('B', fixed)
        self.advance('B-start', 'B-read')
        b_id = session.endpoint.lookup_attempt('B').attempt_id
        session = self.reopen()
        confirmation = session.endpoint.release_attempt(a_id)
        self.assertEqual(confirmation.attempt_id, a_id)
        self.assertTrue(confirmation.already_released)
        self.assertEqual(session.endpoint.slot_owner(3), b_id, MARKER)
        self.assertEqual(session.endpoint.read_terminal(b_id).raw_value, 23, MARKER)
        session.advance_recording()
        self.assertEqual(load_progress(session.business, 'A').phase, 'released')
        self.assertEqual(self.receipt('B', marker=MARKER).attempt_id, b_id)

    def test_release_rejects_unknown_or_running_identity(self):
        session = self.open(REUSE)
        session.submit_order('A', self.configuration())
        self.advance('A-start')
        active = session.endpoint.lookup_attempt('A')
        for attempt_id in ('missing', active.attempt_id):
            with self.assertRaises(ReleaseRejected):
                session.endpoint.release_attempt(attempt_id)
        self.assertEqual(session.endpoint.slot_owner(3), active.attempt_id)
        self.assertEqual(session.endpoint.position(), 1)

    def test_reopen_resubmit(self):
        session = self.open(REUSE)
        fixed = self.configuration()
        session.submit_order('A', fixed)
        self.advance('A-start')
        attempt = session.endpoint.lookup_attempt('A')
        session = self.reopen()
        self.assertEqual(session.submit_order('A', fixed), session.read_order('A'))
        self.assertEqual(session.endpoint.lookup_attempt('A'), attempt)
        self.advance('A-read')
        final, = session.advance_recording()
        session = self.reopen()
        self.assertEqual(session.submit_order('A', fixed), final)
        self.assertEqual(session.advance_recording(), ())
        self.assertEqual(self.counts(), (1, 1))

    def test_joint_handover(self):
        session, first, a_id = self.measured_a(HANDOVER)
        session.request_withdrawal('A')
        pending, = session.advance_recording('A-withdraw')
        self.assertEqual((pending.state, pending.receipt_id), ('pending_review', None), MARKER)
        self.assert_commit_fails(session)
        self.assertIsNone(session.read_order('A').receipt_id, MARKER)
        self.assertEqual(session.endpoint.read_terminal(a_id).raw_value, 17, MARKER)
        session = self.reopen()
        session.advance_recording(order_id='A', lose_release_reply=True)
        a = self.receipt('A', marker=MARKER)
        self.assertEqual((a.raw_value, a.micro_units, a.quality, a.measurement_event_id),
                         (17, 35, 'in_range', 'A-read'), MARKER)
        night = self.configuration(name='R2', scale=3, offset=0, upper=50)
        session.submit_order('B', night)
        self.advance('B-start', 'B-read')
        b_id = session.endpoint.lookup_attempt('B').attempt_id
        session = self.reopen()
        session.advance_recording(order_id='A')
        self.assertEqual(session.endpoint.slot_owner(3), b_id, MARKER)
        self.assertEqual(session.endpoint.read_terminal(b_id).raw_value, 23, MARKER)
        self.assertIsNone(session.read_order('B').receipt_id)
        session.advance_recording(order_id='B')
        b = self.receipt('B', marker=MARKER)
        self.assertEqual((b.raw_value, b.micro_units, b.quality, b.measurement_event_id),
                         (23, 69, 'out_of_range', 'B-read'), MARKER)
        self.assertEqual(session.submit_order('A', first).receipt_id, a.receipt_id)
        self.assertEqual(session.submit_order('B', night).receipt_id, b.receipt_id)
        self.assertEqual(self.receipt('A', marker=MARKER), a, MARKER)
        self.assertEqual(self.counts(), (2, 2), MARKER)
        session.submit_order('C', night)
        self.advance('C-start')
        session.request_withdrawal('C')
        self.advance('C-withdraw', 'C-read')
        session.advance_recording(order_id='C')
        c = self.receipt('C', marker=MARKER)
        self.assertEqual((c.outcome, c.raw_value, c.micro_units, c.quality, c.measurement_event_id),
                         ('withdrawn', None, None, None, None), MARKER)
        self.assertEqual((session.endpoint.position(), self.counts()), (8, (3, 3)))
