"""Public import compatibility; no harness SDK is required."""
import unittest

from agent_session_analyzer import DshAdapter, DshRecorder, OpenHandsAdapter, OpenHandsRecorder
from agent_session_analyzer.adapter import DshAdapter as LegacyDshAdapter, SCHEMA
from agent_session_analyzer.recorder import DshRecorder as LegacyDshRecorder
from agent_session_analyzer.openhands_adapter import OpenHandsAdapter as LegacyOpenHandsAdapter
from agent_session_analyzer.openhands_recorder import OpenHandsRecorder as LegacyOpenHandsRecorder
from agent_session_analyzer.adapters.dsh import DshAdapter as NestedDshAdapter
from agent_session_analyzer.adapters.openhands import OpenHandsAdapter as NestedOpenHandsAdapter
from agent_session_analyzer.recorders.dsh import DshRecorder as NestedDshRecorder
from agent_session_analyzer.recorders.openhands import OpenHandsRecorder as NestedOpenHandsRecorder
from agent_session_analyzer.schema import SCHEMA as SharedSchema


class ImportCompatibilityTests(unittest.TestCase):
    def test_legacy_and_public_imports_share_classes(self):
        for public, legacy, nested in (
            (DshAdapter, LegacyDshAdapter, NestedDshAdapter),
            (OpenHandsAdapter, LegacyOpenHandsAdapter, NestedOpenHandsAdapter),
            (DshRecorder, LegacyDshRecorder, NestedDshRecorder),
            (OpenHandsRecorder, LegacyOpenHandsRecorder, NestedOpenHandsRecorder),
        ):
            with self.subTest(name=public.__name__):
                self.assertIs(public, legacy)
                self.assertIs(public, nested)
        self.assertEqual(SCHEMA, SharedSchema)