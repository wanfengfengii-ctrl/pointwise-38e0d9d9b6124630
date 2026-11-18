import sqlite3

from archive.receipts import ReceiptNotFound
from capture.session import open_recording_session
from handover_inputs import ALT, EXPECTED_A_SOURCE, ROOT_RECORDING, STALE, U2, U2_SHA256, U3
from contextlib import nullcontext as _nullcontext

from recorder.events import InvalidRecordingEvent, RecordingEvent
from tests.support import RecordingCase

MARKER = 'SAMPLE_CUSTODY_MISMATCH'


class HandoverCase(RecordingCase):
    def open_handover(self):
        return self.open(ROOT_RECORDING)

    def start_a(self):
        session = self.open_handover()
        fixed = self.configuration()
        session.submit_order('A', fixed)
        self.advance('A-start')
        self.assertEqual(session.endpoint.position(), 1)
        return session, fixed


class SubmitTests(HandoverCase):
    def test_rejects_invalid_packages_without_identity(self):
        session, _ = self.start_a()
        before = session.business.execute(
            'SELECT count(*) FROM handover_packages').fetchone()[0]
        invalid = [
            None,
            [],
            {'package_id': 'X'},
            dict(U2, extra=True),
            dict(U2, package_id=''),
            dict(U2, package_id='  '),
            dict(U2, package_id='ROOT'),
            dict(U2, package_id='X', predecessor_id='X'),
            dict(U2, predecessor_sha256='A' * 64),
            dict(U2, predecessor_sha256='0' * 63),
            dict(U2, predecessor_sha256=12),
            dict(U2, events=[]),
            dict(U2, events={}),
            dict(U2, events=[dict(U2['events'][0], unknown=1)]),
            dict(U2, events=[U2['events'][0], U2['events'][0]]),
            dict(U2, predecessor_id='\ud800'),
        ]
        for package in invalid:
            with self.subTest(package=package), self.assertRaises(ValueError):
                session.submit_handover(package)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM handover_packages').fetchone()[0], before)
        self.assertEqual(session.endpoint.position(), 1)
        self.assertEqual(session.submit_handover(U2), 'accepted')

    def test_same_id_different_content_conflicts(self):
        session, _ = self.start_a()
        self.assertEqual(session.submit_handover(U2), 'accepted')
        altered = dict(U2, events=[dict(U2['events'][0], raw_value=18), U2['events'][1]])
        with self.assertRaises(ValueError):
            session.submit_handover(altered)
        self.assertEqual(session.submit_handover(U2), 'replay')

    def test_acceptance_does_not_advance_device(self):
        session, _ = self.start_a()
        self.assertEqual(session.submit_handover(U2), 'accepted')
        self.assertEqual(session.endpoint.position(), 1)
        self.assertEqual(session.endpoint.trigger_count(), 1)
        self.assertEqual(session.read_order('A').state, 'pending')

    def test_mismatched_predecessor_rejected_when_known(self):
        session, _ = self.start_a()
        self.assertEqual(session.submit_handover(U2), 'accepted')
        with self.assertRaises(ValueError):
            session.submit_handover(STALE)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM handover_packages WHERE package_id = ?', ('STALE',)
        ).fetchone()[0], 0)
        self.assertEqual(session.submit_handover(U2), 'replay')


class WaitingTests(HandoverCase):
    def test_out_of_order_survives_reopen(self):
        session, _ = self.start_a()
        self.assertEqual(session.submit_handover(U3), 'accepted')
        self.assertEqual(session.submit_handover(STALE), 'accepted')
        session = self.reopen()
        self.assertEqual(session.submit_handover(U3), 'replay')
        self.assertEqual(session.submit_handover(STALE), 'replay')
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 1)
        self.assertEqual(session.submit_handover(U2), 'accepted')
        self.assertEqual(session.submit_handover(U2), 'replay')
        self.assertEqual(session.submit_handover(U3), 'replay')

    def test_admitted_successor_waits_for_full_predecessor(self):
        session, _ = self.start_a()
        self.assertEqual(session.submit_handover(U2), 'accepted')
        self.assertEqual(session.submit_handover(U3), 'accepted')
        session.advance_recording('A-read')
        self.assertEqual(session.endpoint.position(), 2)
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 2)
        session.advance_recording('A-repeat')
        session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 4)

    def test_event_id_collision_disqualifies(self):
        session, _ = self.start_a()
        colliding = {
            'package_id': 'COLLIDE', 'predecessor_id': 'ROOT',
            'predecessor_sha256': U2['predecessor_sha256'],
            'events': [{'event_id': 'A-start', 'kind': 'measure',
                        'order_id': 'A', 'raw_value': 5}],
        }
        self.assertEqual(session.submit_handover(colliding), 'accepted')
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-start')
        self.assertEqual(session.endpoint.position(), 1)
        # A non-colliding variant recovers the unique qualified successor.
        clean = {'package_id': 'CLEAN', 'predecessor_id': 'ROOT',
                 'predecessor_sha256': U2['predecessor_sha256'],
                 'events': [{'event_id': 'A-read', 'kind': 'measure',
                             'order_id': 'A', 'raw_value': 17}]}
        self.assertEqual(session.submit_handover(clean), 'accepted')
        session.advance_recording('A-read')
        self.assertEqual(session.endpoint.position(), 2)

    def test_successor_with_read_prefix_event_is_ineligible(self):
        session, _ = self.start_a()
        self.assertEqual(session.submit_handover(U2), 'accepted')
        session.advance_recording('A-read')
        session.advance_recording('A-repeat')
        duplicate = {
            'package_id': 'DUP', 'predecessor_id': 'U2',
            'predecessor_sha256': U2_SHA256,
            'events': [{'event_id': 'A-read', 'kind': 'measure',
                        'order_id': 'A', 'raw_value': 5}],
        }
        self.assertEqual(session.submit_handover(duplicate), 'accepted')
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-read')
        self.assertEqual(session.endpoint.position(), 3)
        self.assertEqual(session.submit_handover(U3), 'accepted')
        session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 4)


class CompetitionTests(HandoverCase):
    def test_competition_stalls_before_binding(self):
        session, _ = self.start_a()
        self.assertEqual(session.submit_handover(U2), 'accepted')
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-repeat')
        self.assertEqual(session.endpoint.position(), 1)
        self.assertFalse(session.endpoint.lookup_attempt('A').released)
        self.assertEqual(session.submit_handover(ALT), 'accepted')
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-read')
        self.assertEqual(session.endpoint.position(), 1)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM package_chain').fetchone()[0], 0)
        self.assertIsNone(session.read_order('A').receipt_id)
        self.assertEqual(session.endpoint.read_terminal(
            session.endpoint.lookup_attempt('A').attempt_id), None)

    def test_competitor_after_bound_position_is_refused(self):
        session, _ = self.start_a()
        self.assertEqual(session.submit_handover(U2), 'accepted')
        pending, = session.advance_recording('A-read')
        self.assertEqual(pending.state, 'pending_review', MARKER)
        self.assertEqual(session.endpoint.position(), 2, MARKER)
        with self.assertRaises(ValueError):
            session.submit_handover(ALT)
        self.assertEqual(session.submit_handover(U2), 'replay', MARKER)
        terminal = session.endpoint.read_terminal(session.endpoint.lookup_attempt('A').attempt_id)
        self.assertEqual((terminal.kind, terminal.raw_value, terminal.event_id),
                         ('measured', 17, 'A-read'), MARKER)

    def test_refusal_survives_reopen(self):
        session, _ = self.start_a()
        session.submit_handover(U2)
        session.advance_recording('A-read')
        session = self.reopen()
        self.assertEqual(session.submit_handover(U2), 'replay')
        with self.assertRaises(ValueError):
            session.submit_handover(ALT)

    def test_binding_survives_reopen(self):
        session, _ = self.start_a()
        session.submit_handover(U2)
        self.assertEqual(session.advance_recording('A-read')[0].state, 'pending_review')
        session = self.reopen()
        self.assertEqual(session.submit_handover(U2), 'replay')
        with self.assertRaises(ValueError):
            session.submit_handover(ALT)


class SettlementTests(HandoverCase):
    def run_sample_sequence(self):
        session, _ = self.start_a()
        session.submit_handover(U3)
        session.submit_handover(STALE)
        session = self.reopen()
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 1)
        self.assertEqual(session.submit_handover(U2), 'accepted')
        self.assertEqual(session.submit_handover(U2), 'replay')
        session.advance_recording('A-read')
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 2)
        session.advance_recording('A-repeat')
        session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 4)
        return session

    def test_failed_publication_keeps_evidence_and_source(self):
        session = self.run_sample_sequence()
        attempt = session.endpoint.lookup_attempt('A')
        terminal_before = session.endpoint.read_terminal(attempt.attempt_id)
        self.assertEqual((terminal_before.kind, terminal_before.raw_value,
                          terminal_before.event_id, terminal_before.event_position),
                         ('measured', 17, 'A-read', 2), MARKER)
        with self.assertRaises(sqlite3.IntegrityError) as failure:
            session.advance_recording(order_id='A', fail_publish=True)
        self.assertEqual(failure.exception.sqlite_errorname, 'SQLITE_CONSTRAINT_FOREIGNKEY')
        with self.assertRaises(ReceiptNotFound):
            session.read_receipt('receipt:A')
        with self.assertRaises(ReceiptNotFound):
            session.read_receipt_source('receipt:A')
        self.assertEqual(session.endpoint.read_terminal(attempt.attempt_id), terminal_before)

        session = self.reopen()
        session.advance_recording(order_id='A')
        receipt = self.receipt('A', marker=MARKER)
        self.assertEqual((receipt.raw_value, receipt.micro_units, receipt.quality),
                         (17, 35, 'in_range'), MARKER)
        self.assertEqual((receipt.terminal_event_id, receipt.terminal_event_position),
                         ('A-read', 2), MARKER)
        source = session.read_receipt_source('receipt:A')
        self.assertEqual(source, EXPECTED_A_SOURCE, MARKER)

        session = self.reopen()
        self.assertEqual(session.read_receipt('receipt:A'), receipt)
        self.assertEqual(session.read_receipt_source('receipt:A'), EXPECTED_A_SOURCE, MARKER)
        self.assertEqual(session.endpoint.trigger_count(), 1)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_source_is_fixed_after_cleanup_and_advances(self):
        session = self.run_sample_sequence()
        session.advance_recording(order_id='A')
        source = session.read_receipt_source('receipt:A')
        self.assertEqual(source, EXPECTED_A_SOURCE, MARKER)
        session.advance_recording(order_id='A')
        session = self.reopen()
        self.assertEqual(session.read_receipt_source('receipt:A'), source)
        self.assertEqual(session.submit_handover(U2), 'replay')
        self.assertEqual(session.read_receipt_source('receipt:A'), source)

    def test_source_read_is_read_only(self):
        session = self.run_sample_sequence()
        session.advance_recording(order_id='A')
        session = self.reopen()
        session.business.execute('PRAGMA query_only = ON')
        before = session.business.total_changes
        self.assertEqual(session.read_receipt_source('receipt:A'), EXPECTED_A_SOURCE)
        self.assertEqual(session.business.total_changes, before)
        with self.assertRaises(ReceiptNotFound):
            session.read_receipt_source('receipt:missing')

    def test_root_terminal_has_no_source(self):
        session = self.open()
        fixed = self.configuration()
        session.submit_order('A', fixed)
        self.advance('A-start', 'A-read')
        session.advance_recording(order_id='A')
        receipt = self.receipt('A')
        self.assertIsNone(session.read_receipt_source(receipt.receipt_id))
        session = self.reopen()
        self.assertIsNone(session.read_receipt_source(receipt.receipt_id))

    def test_package_withdrawal_terminal_source(self):
        session, _ = self.start_a()
        package = {
            'package_id': 'W', 'predecessor_id': 'ROOT',
            'predecessor_sha256': U2['predecessor_sha256'],
            'events': [
                {'event_id': 'A-read', 'kind': 'measure', 'order_id': 'A', 'raw_value': 17},
                {'event_id': 'A-withdraw', 'kind': 'withdraw', 'order_id': 'A'},
            ],
        }
        self.assertEqual(session.submit_handover(package), 'accepted')
        session.submit_order('A', session.read_order('A').configuration_id)
        session.advance_recording('A-read')
        session.request_withdrawal('A')
        session.advance_recording('A-withdraw')
        session.advance_recording(order_id='A')
        receipt = self.receipt('A')
        # The measure was the device's first terminal, before the withdrawal.
        self.assertEqual((receipt.outcome, receipt.terminal_event_id, receipt.terminal_event_position),
                         ('measured', 'A-read', 2))
        source = session.read_receipt_source(receipt.receipt_id)
        self.assertEqual(source, {'package_id': 'W',
                                  'sha256': session.business.execute(
                                      'SELECT sha256 FROM handover_packages WHERE package_id = ?',
                                      ('W',)).fetchone()[0],
                                  'package_position': 1})


class BindingTests(HandoverCase):
    def test_digest_matches_published_sample(self):
        from handover_inputs import (
            ALT as sample_alt, EXPECTED_SHA256, ROOT_RECORDING, STALE as sample_stale,
            U2 as sample_u2, U3 as sample_u3,
        )
        from recorder import handover
        self.assertEqual(handover.root_sha256(ROOT_RECORDING), EXPECTED_SHA256['ROOT'])
        for package in (sample_u2, sample_u3, sample_alt, sample_stale):
            spec = handover.parse_package(package)
            self.assertEqual(spec.sha256, EXPECTED_SHA256[package['package_id']])

    def test_error_event_leaves_no_binding(self):
        session, _ = self.start_a()
        session.submit_handover(U2)
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 1)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM package_chain').fetchone()[0], 0)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM event_sources').fetchone()[0], 0)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM capture_progress WHERE attempt_id IS NOT NULL').fetchone()[0], 1)

    def test_first_event_failure_does_not_bind(self):
        session = self.open((RecordingEvent('A-start', 'trigger', 'A', slot=3),))
        fixed = self.configuration()
        session.submit_order('A', fixed)
        session.advance_recording('A-start')
        from recorder import handover
        digest = handover.root_sha256(session.endpoint.recording)
        occupied = {
            'package_id': 'OCC', 'predecessor_id': 'ROOT', 'predecessor_sha256': digest,
            'events': [{'event_id': 'B-start', 'kind': 'trigger', 'order_id': 'B', 'slot': 3}],
        }
        self.assertEqual(session.submit_handover(occupied), 'accepted')
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('B-start')
        self.assertEqual(session.endpoint.position(), 1)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM package_chain').fetchone()[0], 0)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM event_sources').fetchone()[0], 0)
        self.assertIsNone(session.endpoint.lookup_attempt('B'))
        # A package whose first event is valid may still be admitted; with two
        # eligible successors the next advance stalls instead of guessing.
        free = {'package_id': 'FREE', 'predecessor_id': 'ROOT',
                'predecessor_sha256': digest,
                'events': [{'event_id': 'B-start', 'kind': 'trigger', 'order_id': 'B', 'slot': 4}]}
        self.assertEqual(session.submit_handover(free), 'accepted')
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('B-start')
        self.assertEqual(session.endpoint.position(), 1)


class DeferredDigestTests(HandoverCase):
    def test_unknown_predecessor_waits_even_with_wrong_digest(self):
        session, _ = self.start_a()
        # The predecessor is unknown: persist the promise and wait.
        self.assertEqual(session.submit_handover(STALE), 'accepted')
        session = self.reopen()
        # Once the predecessor's real digest is known the wrong promise loses
        # its advance qualification, yet a resubmission is still a replay.
        self.assertEqual(session.submit_handover(U2), 'accepted')
        session.advance_recording('A-read')
        session.advance_recording('A-repeat')
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 3)
        self.assertEqual(session.submit_handover(STALE), 'replay')
        self.assertEqual(session.submit_handover(U3), 'accepted')
        session.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 4)

    def test_wrong_digest_with_known_predecessor_rejected(self):
        session, _ = self.start_a()
        self.assertEqual(session.submit_handover(U2), 'accepted')
        before = session.business.execute(
            'SELECT count(*) FROM handover_packages').fetchone()[0]
        with self.assertRaises(ValueError):
            session.submit_handover(STALE)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM handover_packages').fetchone()[0], before)

    def test_named_event_cannot_bypass_competition(self):
        session, _ = self.start_a()
        session.submit_handover(U2)
        session.submit_handover(ALT)
        # Both packages start with A-read; naming it cannot pick a winner.
        with self.assertRaises(InvalidRecordingEvent):
            session.advance_recording('A-read')
        self.assertEqual(session.endpoint.position(), 1)
        self.assertEqual(session.endpoint.trigger_count(), 1)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM package_chain').fetchone()[0], 0)


class TwoSessionTests(HandoverCase):
    def test_second_session_refuses_after_commit(self):
        session, _ = self.start_a()
        session.submit_handover(U2)
        other = open_recording_session(self.workspace, ROOT_RECORDING)
        self.addCleanup(other.close)
        session.advance_recording('A-read')
        # The other connection sees the committed binding and refuses ALT.
        with self.assertRaises(ValueError):
            other.submit_handover(ALT)
        self.assertEqual(other.submit_handover(U2), 'replay')
        self.assertEqual(other.endpoint.position(), 2)

    def test_out_of_order_across_sessions(self):
        session, _ = self.start_a()
        other = open_recording_session(self.workspace, ROOT_RECORDING)
        self.addCleanup(other.close)
        self.assertEqual(other.submit_handover(U3), 'accepted')
        self.assertEqual(session.submit_handover(U3), 'replay')
        self.assertEqual(session.submit_handover(U2), 'accepted')
        with self.assertRaises(InvalidRecordingEvent):
            other.advance_recording('A-confirm')
        session.advance_recording('A-read')
        with self.assertRaises(InvalidRecordingEvent):
            other.advance_recording('A-confirm')
        self.assertEqual(other.endpoint.position(), 2)
        session.advance_recording('A-repeat')
        other.advance_recording('A-confirm')
        self.assertEqual(session.endpoint.position(), 4)


class SampleInputTests(RecordingCase):
    def test_published_sample_inputs_check(self):
        import handover_inputs
        handover_inputs.check_inputs()


class CrashWindowTests(HandoverCase):
    def test_device_binding_heals_business_mirror_on_reopen(self):
        from recorder import handover
        session, _ = self.start_a()
        session.submit_handover(U2)
        resolved = handover.resolve_next_event(
            session.business, session.endpoint.recording, 2, 'A-read')
        from recorder.replies import ReplyLost
        with self.assertRaises(ReplyLost):
            session.endpoint.advance_resolved(resolved)
        # The device committed the position and binding; the business mirror
        # did not.  Reopening must recover the fixed source from the device.
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM event_sources').fetchone()[0], 0)
        self.assertEqual(len(session.endpoint.package_bindings()), 1)
        session.business.execute('DELETE FROM capture_progress')
        session.business.execute('DELETE FROM sampling_orders')
        session = self.reopen()
        self.assertEqual(session.business.execute(
            'SELECT position, package_id, package_position FROM event_sources'
        ).fetchall(), [(2, 'U2', 1)])
        self.assertEqual(session.business.execute(
            'SELECT package_id, start_position, event_count FROM package_chain'
        ).fetchall(), [('U2', 2, 2)])

    def test_same_session_heals_without_reopen(self):
        from recorder import handover
        from recorder.replies import ReplyLost
        session, _ = self.start_a()
        session.submit_handover(U2)
        resolved = handover.resolve_next_event(
            session.business, session.endpoint.recording, 2, 'A-read')
        with self.assertRaises(ReplyLost):
            session.endpoint.advance_resolved(resolved)
        self.assertEqual(session.business.execute(
            'SELECT count(*) FROM event_sources').fetchone()[0], 0)
        # The same session reconnects the mirror before its next advance.
        session.advance_recording('A-repeat')
        self.assertEqual(session.endpoint.position(), 3)
        self.assertEqual(session.business.execute(
            'SELECT position, package_id, package_position FROM event_sources ORDER BY position'
        ).fetchall(), [(2, 'U2', 1), (3, 'U2', 2)])

    def test_lost_business_commit_still_settles_with_source(self):
        from recorder import handover
        from recorder.replies import ReplyLost
        session, _ = self.start_a()
        session.submit_handover(U2)
        resolved = handover.resolve_next_event(
            session.business, session.endpoint.recording, 2, 'A-read')
        with self.assertRaises(ReplyLost):
            session.endpoint.advance_resolved(resolved)
        # Simulate a stop after the device committed A-read but before the
        # business mirror and progress were written.
        session.business.execute('DELETE FROM capture_progress')
        session.business.execute('DELETE FROM sampling_orders')
        session = self.reopen()
        self.assertEqual(session.business.execute(
            'SELECT position, package_id, package_position FROM event_sources'
        ).fetchall(), [(2, 'U2', 1)])
        # Recreate the order intent; progress is rebuilt by the operator's
        # normal recovery step, and the device terminal still settles.
        fixed = self.configuration()
        session.submit_order('A', fixed)
        session.advance_recording(order_id='A')
        receipt = self.receipt('A', marker=MARKER)
        self.assertEqual((receipt.raw_value, receipt.micro_units, receipt.terminal_event_id,
                          receipt.terminal_event_position), (17, 35, 'A-read', 2), MARKER)
        self.assertEqual(session.read_receipt_source('receipt:A'), EXPECTED_A_SOURCE)
