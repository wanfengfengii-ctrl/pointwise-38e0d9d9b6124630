import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from capture.session import open_recording_session
from catalog.calibration import Calibration
from catalog.channels import ChannelKey
from catalog.limits import QualityLimits
from catalog.revisions import ChannelRevision
from recorder.events import load_recording


def receipt_for(session, order_id):
    status = session.read_order(order_id)
    if status.receipt_id is None:
        raise AssertionError(f'SAMPLE_CUSTODY_MISMATCH: no receipt for {order_id}')
    return session.read_receipt(status.receipt_id)


def sample_fields(receipt):
    return [receipt.outcome, receipt.raw_value, receipt.micro_units,
            receipt.quality, receipt.measurement_event_id]


def run_handover(workspace):
    recording = load_recording(Path(__file__).resolve().parent / 'recordings' / 'handover.json')
    channel = ChannelKey('north-bank', 'K1')
    with open_recording_session(workspace, recording) as session:
        day = session.add_channel_revision(ChannelRevision(channel, 'R1', Calibration(2, 1),
                                                           QualityLimits(0, 35)))
        session.submit_order('A', day)
        session.advance_recording('A-start')
        session.advance_recording('A-read')
        session.request_withdrawal('A')
        pending, = session.advance_recording('A-withdraw')
        try:
            session.advance_recording(order_id='A', fail_publish=True)
        except sqlite3.IntegrityError as error:
            if error.sqlite_errorname != 'SQLITE_CONSTRAINT_FOREIGNKEY':
                raise
        else:
            raise AssertionError('expected the publication commit to fail')
        visible_after_failure = session.read_order('A').receipt_id is not None
        original_id = session.endpoint.lookup_attempt('A').attempt_id
        held = session.endpoint.read_terminal(original_id)
        raw_after_failure = None if held is None else held.raw_value

    with open_recording_session(workspace, recording) as session:
        session.advance_recording(order_id='A', lose_release_reply=True)
        old = receipt_for(session, 'A')
        slot_after_old_release = session.endpoint.slot_owner(3)
        night = session.add_channel_revision(ChannelRevision(channel, 'R2', Calibration(3, 0),
                                                             QualityLimits(0, 50)))
        session.submit_order('B', night)
        session.advance_recording('B-start')
        session.advance_recording('B-read')

    with open_recording_session(workspace, recording) as session:
        session.advance_recording(order_id='A')
        b_id = session.endpoint.lookup_attempt('B').attempt_id
        b_terminal = session.endpoint.read_terminal(b_id)
        b_raw_after_retry = None if b_terminal is None else b_terminal.raw_value
        b_still_owns_slot = session.endpoint.slot_owner(3) == b_id
        session.advance_recording(order_id='B')
        new = receipt_for(session, 'B')
        repeats = [session.submit_order('A', day).receipt_id, session.submit_order('B', night).receipt_id]
        unchanged = session.read_receipt(old.receipt_id) == old
        session.submit_order('C', night)
        session.advance_recording('C-start')
        session.request_withdrawal('C')
        session.advance_recording('C-withdraw')
        session.advance_recording('C-read')
        session.advance_recording(order_id='C')
        cancelled = receipt_for(session, 'C')
        receipt_count = session.business.execute('SELECT count(*) FROM receipts').fetchone()[0]
        actual = {'A': sample_fields(old), 'B': sample_fields(new), 'C': sample_fields(cancelled)}
        downstream_actual = {
            'withdrawal_state': pending.state, 'withdrawal_receipt': pending.receipt_id,
            'visible_after_failed_commit': visible_after_failure, 'raw_after_failed_commit': raw_after_failure,
            'slot_after_old_release': slot_after_old_release, 'B_raw_after_A_retry': b_raw_after_retry,
            'B_still_owns_slot': b_still_owns_slot, 'A_receipt_unchanged': unchanged,
            'reused_receipts': repeats == [old.receipt_id, new.receipt_id],
            'trigger_count': session.endpoint.trigger_count(), 'receipt_count': receipt_count,
            'recording_position': session.endpoint.position(),
        }

    expected = {
        'A': ['measured', 17, 35, 'in_range', 'A-read'],
        'B': ['measured', 23, 69, 'out_of_range', 'B-read'],
        'C': ['withdrawn', None, None, None, None],
    }
    downstream_expected = {
        'withdrawal_state': 'pending_review', 'withdrawal_receipt': None,
        'visible_after_failed_commit': False, 'raw_after_failed_commit': 17,
        'slot_after_old_release': None, 'B_raw_after_A_retry': 23,
        'B_still_owns_slot': True, 'A_receipt_unchanged': True,
        'reused_receipts': True, 'trigger_count': 3, 'receipt_count': 3, 'recording_position': 8,
    }
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'SAMPLE_CUSTODY_MISMATCH: {actual!r}, {downstream_actual!r}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v7.joint_handover',
        'input': {'recording': 'recordings/handover.json', 'slot': 3,
                  'A': {'raw': 17, 'calibration': [2, 1], 'limits': [0, 35]},
                  'B': {'raw': 23, 'calibration': [3, 0], 'limits': [0, 50]},
                  'C': {'events': ['C-withdraw', 'C-read']},
                  'interruption': ['A-withdraw reply loss', 'failed publication commit', 'A-release reply loss']},
        'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': ['capture/session.py', 'capture/reconcile.py', 'recorder/endpoint.py',
                       'capture/release.py', 'archive/settle.py', 'archive/publication.py', 'archive/quality.py'],
        'oracle': 'Hand calculation 17*2+1=35 and 23*3=69 using closed limits. C withdrawal precedes its read. An old release acknowledgement leaves the current B identity and raw value intact.',
    }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    with TemporaryDirectory(prefix='north-bank-') as workspace:
        run_handover(workspace)
