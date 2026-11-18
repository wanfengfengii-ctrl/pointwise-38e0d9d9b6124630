from sqlite3 import Connection

from archive.ledger import find_release_authorization
from capture.progress import CaptureBindingConflict, load_progress, mark_release_confirmed
from recorder.endpoint import RecordingEndpoint
from recorder.replies import ReplyLost


def resume_release(
    db: Connection, endpoint: RecordingEndpoint, order_id: str, *, lose_reply: bool = False,
) -> bool:
    progress = load_progress(db, order_id)
    if progress is None or progress.attempt_id is None:
        return False
    if progress.phase == 'released':
        return True
    attempt_id = progress.attempt_id
    if find_release_authorization(db, order_id, attempt_id) is None:
        return False
    try:
        reply = endpoint.release_attempt(attempt_id, lose_reply=lose_reply)
    except ReplyLost:
        return False
    if reply.attempt_id != attempt_id:
        raise CaptureBindingConflict(f'device confirmed a different release for {order_id!r}')
    with db:
        db.execute('BEGIN IMMEDIATE')
        mark_release_confirmed(db, order_id, attempt_id)
    return True
