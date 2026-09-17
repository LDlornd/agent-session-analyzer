"""Public exports refer to the canonical implementations; no SDK required."""
import unittest

from agent_session_analyzer import DshAdapter, DshRecorder, OpenHandsAdapter, OpenHandsRecorder
from agent_session_analyzer.adapters.dsh import DshAdapter as NestedDshAdapter
from agent_session_analyzer.adapters.openhands import OpenHandsAdapter as NestedOpenHandsAdapter
from agent_session_analyzer.recorders.dsh import DshRecorder as NestedDshRecorder
from agent_session_analyzer.recorders.openhands import OpenHandsRecorder as NestedOpenHandsRecorder


class PublicImportTests(unittest.TestCase):
    def test_public_exports_share_canonical_classes(self):
        for public, nested in (
            (DshAdapter, NestedDshAdapter),
            (OpenHandsAdapter, NestedOpenHandsAdapter),
            (DshRecorder, NestedDshRecorder),
            (OpenHandsRecorder, NestedOpenHandsRecorder),
        ):
            with self.subTest(name=public.__name__):
                self.assertIs(public, nested)
