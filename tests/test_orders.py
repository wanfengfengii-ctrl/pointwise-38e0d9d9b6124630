from capture.progress import load_progress
from catalog.revisions import UnknownConfiguration
from orders.identity import OrderConflict
from orders.store import UnknownOrder
from tests.support import RecordingCase


class OrderTests(RecordingCase):
    def test_conflicting_resubmission(self):
        session = self.open()
        first = self.configuration()
        second = self.configuration(name='R2', scale=3)
        original = session.submit_order('A', first)
        self.assertEqual(session.submit_order('A', first), original)
        self.advance('A-start')
        before = session.read_order('A')
        attempt = session.endpoint.lookup_attempt('A')
        with self.assertRaises(OrderConflict):
            session.submit_order('A', second)
        self.assertEqual(session.read_order('A'), before)
        self.assertEqual(session.read_order('A').configuration_id, first)
        self.assertEqual(session.endpoint.lookup_attempt('A'), attempt)
        self.assertEqual(self.counts(), (1, 0))

    def test_same_intent_retry(self):
        session = self.open()
        fixed = self.configuration()
        initial = session.submit_order('A', fixed)
        self.assertEqual(session.submit_order('A', fixed), initial)
        self.assertIsNone(load_progress(session.business, 'A'))
        self.assertEqual(self.counts(), (0, 0))
        self.advance('A-start', 'A-read')
        current = session.advance_recording()[0]
        self.assertEqual(session.submit_order('A', fixed), current)
        self.assertEqual(self.counts(), (1, 1))

    def test_unknown_configuration(self):
        session = self.open()
        with self.assertRaises(UnknownConfiguration):
            session.submit_order('A', 'unknown-configuration')
        with self.assertRaises(UnknownOrder):
            session.read_order('A')
        self.assertIsNone(load_progress(session.business, 'A'))
        self.assertEqual(session.endpoint.position(), 0)
        self.assertEqual(self.counts(), (0, 0))
