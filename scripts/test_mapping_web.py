"""Loopback mapping contracts: mock worker only; no ROS, DDS or hardware."""
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from guide_mapping import MappingManager, validate_session
from test_guide_mapping import bundle
import test_guide_points as point_tests


class MappingWebTests(unittest.TestCase):
    request = point_tests.WebPointTests.request

    def setUp(self):
        point_tests.WebPointTests.setUp(self)
        self.project = Path(self.directory.name)
        self.release = threading.Event()
        self.process = Mock()
        self.process.poll.side_effect = lambda: 0 if self.release.is_set() else None
        self.process.returncode = 0
        self.launch = Mock(return_value=self.process)
        self.server.mapping = MappingManager(self.project, launcher=self.launch,
            offline=Mock(), idle=lambda: not self.tasks.active() and not self.server.initialization.active()
                and not self.server.relocation.active())

    def tearDown(self):
        self.server.mapping.cancel()
        self.release.set()
        if self.server.mapping.thread:
            self.server.mapping.thread.join(2)
        point_tests.WebPointTests.tearDown(self)

    def start(self):
        code, body = self.request('/api/mapping/start',
            {'robot_ready': True, 'manual_only': True, 'pin': '246810'})
        self.assertEqual(code, 200, body)
        return body['mapping']['session_id']

    def test_reads_and_invalid_requests_never_launch_worker(self):
        code, body = self.request('/api/mapping')
        self.assertEqual(code, 200)
        self.assertEqual(body['mapping']['state'], 'idle')
        for payload in ({}, {'robot_ready': True, 'manual_only': True},
                        {'robot_ready': False, 'manual_only': True, 'pin': '246810'},
                        {'robot_ready': True, 'manual_only': True, 'pin': '246810', 'command': 'shell'}):
            self.assertEqual(self.request('/api/mapping/start', payload)[0], 400)
        self.launch.assert_not_called()
        self.tasks.start.assert_not_called()

    def test_current_session_stationary_save_and_review(self):
        session = self.start()
        manager = self.server.mapping
        (manager.directory/'status.json').write_text(json.dumps({'state': 'mapping', 'message': 'mock collecting'}))
        self.assertEqual(self.request('/api/mapping')[1]['mapping']['state'], 'mapping')
        for payload in ({'session_id': 'wrong', 'stationary': True, 'pin': '246810'},
                        {'session_id': session, 'stationary': False, 'pin': '246810'}):
            self.assertEqual(self.request('/api/mapping/finish', payload)[0], 400)
        code, body = self.request('/api/mapping/finish',
            {'session_id': session, 'stationary': True, 'pin': '246810'})
        self.assertEqual(code, 200, body)
        bundle(manager.directory)
        self.release.set()
        manager.thread.join(2)
        self.assertEqual(manager.snapshot()['state'], 'review')
        self.assertFalse((self.project/'map').exists())

    def test_mapping_blocks_initialization_pose_and_plans(self):
        self.start()
        self.assertEqual(self.request('/api/init/start', {'robot_ready': True, 'pin': '246810'})[0], 503)
        with self.assertRaisesRegex(RuntimeError, '建图'):
            self.server.check_mapping_idle()
        self.assertEqual(self.request('/api/points/pose')[0], 503)
        self.tasks.start.assert_not_called()

    def test_cancel_has_no_pin_requirement_and_preserves_old_map(self):
        bundle(self.project)
        original = (self.project/'map/map.pgm').read_bytes()
        self.start()
        self.assertEqual(self.request('/api/mapping/cancel', {})[0], 200)
        self.release.set()
        self.server.mapping.thread.join(2)
        self.assertEqual(self.server.mapping.snapshot()['state'], 'cancelled')
        self.assertEqual((self.project/'map/map.pgm').read_bytes(), original)
        self.assertEqual(self.request('/api/mapping/cancel', {'force': True})[0], 400)

    def test_activation_invalidates_old_initialization_and_previews(self):
        manager = self.server.mapping
        directory = self.project/'run/mapping'/('f'*32)
        bundle(directory)
        manager.directory = directory
        manager.state = {'state': 'review', 'session_id': 'f'*32, 'validation': validate_session(directory)}
        self.server.initialization.state = {'state': 'succeeded'}
        self.server.pending['old'] = {'plan': 'stale'}
        code, body = self.request('/api/mapping/activate',
            {'session_id': 'f'*32, 'map_reviewed': True, 'pin': '246810'})
        self.assertEqual(code, 200, body)
        self.assertEqual(self.server.initialization.snapshot()['state'], 'idle')
        self.assertTrue(self.server.initialization.snapshot()['needs_initial_pose'])
        self.assertEqual(self.server.pending, {})
        self.tasks.start.assert_not_called()

    def test_operator_setup_updates_permission_but_never_arms(self):
        values = {'GUIDE_OPERATOR_PIN': '112233', 'GUIDE_MOTION_ENABLED': '1',
                  'GUIDE_NAMED_NAV_VERIFIED': '1', 'GUIDE_ARM_ENABLED': '0'}
        payload = {'new_pin': '112233', 'enable_navigation': True, 'module_reviewed': True, 'pin': '246810'}
        with patch.object(self.web, 'configure_operator', return_value=values) as configure, \
                patch.dict('os.environ', {}):
            self.assertEqual(self.request('/api/setup/operator', {**payload, 'pin': 'wrong'})[0], 400)
            configure.assert_not_called()
            self.assertEqual(self.request('/api/setup/operator', payload)[0], 200)
            configure.assert_called_once()
            self.assertEqual(self.server.operator_pin, '112233')
            self.assertTrue(self.server.motion_enabled)
        self.tasks.start.assert_not_called()

    def test_disabled_audio_never_initializes_sdk_or_accepts_speech(self):
        audio = self.web.DisabledSpeechWorker()
        audio.start()
        self.assertTrue(audio.ready.is_set())
        self.assertTrue(audio.disabled)
        with self.assertRaises(RuntimeError):
            audio.enqueue('do not speak')
        audio.stop()


if __name__ == '__main__':
    unittest.main()
