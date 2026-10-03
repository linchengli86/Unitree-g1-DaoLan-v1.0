"""No ROS/hardware: mapping state, registry isolation and rollback tests."""
import copy
import fcntl
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from guide_mapping import FILES, MappingManager, validate_session, session_running
from guide_operator_setup import configure_operator
import setup_pc2


def bundle(root, marker=b'0 0 0'):
    for name in FILES:
        target = root/name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith('.pcd'):
            target.write_bytes(b'VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nWIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n'+marker+b'\n')
        elif name.endswith('.pgm'):
            target.write_bytes(b'P5\n2 2\n255\n'+bytes([0,254,205,254]))
        elif name.endswith('.yaml'):
            target.write_text('image: map.pgm\nresolution: 0.05\norigin: [0,0,0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n')
        else:
            target.write_text('0 0 0 0 0 0 0 1\n')


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.candidate = self.project/'run/mapping'/('a'*32)
        bundle(self.candidate)
        self.manager = MappingManager(self.project, offline=Mock())
        self.manager.directory = self.candidate
        self.manager.state = {'state':'review','session_id':'a'*32,'validation':validate_session(self.candidate)}

    def tearDown(self):
        self.temp.cleanup()

    def activate(self):
        return self.manager.activate({'session_id':'a'*32,'map_reviewed':True})

    def test_structure_and_preview(self):
        result = validate_session(self.candidate)
        self.assertEqual(set(result['files']), set(FILES))
        self.assertTrue(result['geometric_review_required'])
        self.assertTrue(self.manager.preview().startswith(b'\x89PNG'))

    def test_missing_empty_malformed_pcd_rejected(self):
        path = self.candidate/FILES[2]
        for content in (b'',b'not PCD',b'POINTS 0\nDATA ascii\n'):
            path.write_bytes(content)
            with self.assertRaises(ValueError):validate_session(self.candidate)

    def test_bad_keyposes_and_yaml_rejected(self):
        (self.candidate/FILES[4]).write_text('0 nan 0 0 0 0 0 1\n')
        with self.assertRaises(ValueError):validate_session(self.candidate)
        bundle(self.candidate)
        (self.candidate/'map/map.yaml').write_text('image: ../../escape.pgm\n')
        with self.assertRaises(ValueError):validate_session(self.candidate)

    def test_symlink_rejected(self):
        path = self.candidate/FILES[2]
        path.unlink()
        path.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):validate_session(self.candidate)

    def test_activation_backs_up_map_and_archives_old_points(self):
        bundle(self.project,b'9 9 9')
        (self.project/'map/map.json').write_text('{"old":"editing"}')
        (self.project/'config').mkdir()
        points = self.project/'config/guide_points.json'
        points.write_text('{"old":"points"}')
        original = (self.project/FILES[2]).read_bytes()
        result = self.activate()
        self.assertEqual(result['state'],'succeeded')
        backup = self.project/result['backup']
        self.assertEqual((backup/FILES[2]).read_bytes(),original)
        self.assertEqual((backup/'config/guide_points.json').read_text(),'{"old":"points"}')
        self.assertFalse(points.exists())
        self.assertTrue((backup/'map/map.json').exists())
        self.assertFalse((self.project/'map/map.json').exists())
        self.assertTrue((self.project/'map/active_version.json').exists())

    def test_wrong_session_review_false_and_extra_fields_rejected(self):
        for payload in ({}, {'session_id':'b'*32,'map_reviewed':True},
                        {'session_id':'a'*32,'map_reviewed':False},
                        {'session_id':'a'*32,'map_reviewed':True,'force':True}):
            with self.assertRaises(ValueError):self.manager.activate(payload)

    def test_busy_offline_and_changed_map_rejected_without_writes(self):
        self.manager.idle=lambda:False
        with self.assertRaises(RuntimeError):self.activate()
        self.manager.idle=lambda:True
        self.manager.offline=Mock(side_effect=RuntimeError('ROS still live'))
        with self.assertRaises(RuntimeError):self.activate()
        self.manager.offline=Mock()
        (self.candidate/FILES[2]).write_bytes((self.candidate/FILES[2]).read_bytes()+b'changed')
        with self.assertRaises(ValueError):self.activate()
        self.assertFalse((self.project/'map').exists())

    def test_mid_install_failure_rolls_back(self):
        bundle(self.project,b'9 9 9')
        originals={name:(self.project/name).read_bytes() for name in FILES}
        original_replace=os.replace
        def fail_once(src,dst):
            if str(dst).endswith(FILES[2]):raise OSError('injected failure')
            return original_replace(src,dst)
        with patch('guide_mapping.os.replace',side_effect=fail_once):
            with self.assertRaises(OSError):self.activate()
        self.assertEqual(self.manager.snapshot()['state'],'review')
        for name, data in originals.items():self.assertEqual((self.project/name).read_bytes(),data)
        self.assertEqual(self.activate()['state'], 'succeeded')

    def test_worker_lock_blocks_restart_and_allows_cancel(self):
        root=self.project/'run/mapping'
        (root/'current.json').write_text(json.dumps({'session_id':'a'*32}))
        with (root/'session.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(session_running(self.project))
            recovered=MappingManager(self.project, offline=Mock())
            self.assertTrue(recovered.active())
            with self.assertRaises(RuntimeError):recovered.start({'robot_ready':True,'manual_only':True})
            self.assertTrue(recovered.cancel())
            self.assertTrue((self.candidate/'cancel.request').exists())
            self.assertEqual(recovered.snapshot()['state'],'stopping')
        recovered.thread.join(2)
        self.assertFalse(recovered.active())
        self.assertEqual(recovered.snapshot()['state'],'cancelled')

    def test_start_requires_exact_physical_confirmation(self):
        for payload in ({},{'robot_ready':1,'manual_only':True},{'robot_ready':False,'manual_only':True},
                        {'robot_ready':True,'manual_only':True,'x':1}):
            with self.assertRaises(ValueError):self.manager.start(payload)

    def test_cancel_during_slow_launch_does_not_release_interlock(self):
        entered, release = threading.Event(), threading.Event()
        def slow_launch(_directory):
            entered.set()
            release.wait(2)
            return Mock(poll=Mock(return_value=0), returncode=0)
        manager=MappingManager(self.project, launcher=slow_launch, offline=Mock())
        try:
            manager.start({'robot_ready':True,'manual_only':True})
            self.assertTrue(entered.wait(1))
            manager.cancel()
            self.assertTrue(manager.active())
            self.assertEqual(manager.snapshot()['state'],'stopping')
        finally:
            release.set()
            manager.thread.join(2)
        self.assertEqual(manager.snapshot()['state'],'cancelled')

    def test_pending_review_cannot_be_overwritten_and_cancel_preserves_files(self):
        with self.assertRaises(RuntimeError):self.manager.start({'robot_ready':True,'manual_only':True})
        data=(self.candidate/FILES[2]).read_bytes()
        self.assertTrue(self.manager.cancel())
        self.assertEqual(self.manager.snapshot()['state'],'cancelled')
        self.assertEqual((self.candidate/FILES[2]).read_bytes(),data)

    def test_finish_only_current_mapping_session_and_stationary(self):
        self.manager.state['state']='mapping'
        for payload in ({},{'session_id':'b'*32,'stationary':True},{'session_id':'a'*32,'stationary':False}):
            with self.assertRaises(ValueError):self.manager.finish(payload)
        result=self.manager.finish({'session_id':'a'*32,'stationary':True})
        self.assertEqual(result['state'],'saving')
        self.assertTrue((self.candidate/'finish.request').exists())


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.project=Path(self.temp.name)
        (self.project/'scripts').mkdir()
        (self.project/'scripts/omni.env.example').write_text('DASHSCOPE_API_KEY=\nGUIDE_MOTION_ENABLED=0\n')

    def tearDown(self):self.temp.cleanup()

    def test_operator_preserves_key_and_does_not_arm(self):
        path=self.project/'run/config/omni.env'
        path.parent.mkdir(parents=True)
        path.write_text('DASHSCOPE_API_KEY=placeholder\nGUIDE_MOTION_ENABLED=0\n')
        path.chmod(0o600)
        result=configure_operator(self.project,{'new_pin':'123456','enable_navigation':True,'module_reviewed':True})
        self.assertIn('DASHSCOPE_API_KEY=placeholder',path.read_text())
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(result['GUIDE_ARM_ENABLED'],'0')
        self.assertEqual(result['GUIDE_NAMED_NAV_VERIFIED'],'1')

    def test_bad_pin_unknown_fields_and_unreviewed_rejected(self):
        base={'new_pin':'123456','enable_navigation':True,'module_reviewed':True}
        for data in ({**base,'new_pin':'123'}, {**base,'new_pin':'x; command'},
                     {**base,'module_reviewed':False},{**base,'enable_navigation':1},{**base,'shell':'x'}):
            with self.assertRaises(ValueError):configure_operator(self.project,data)

    def test_supported_platform_only(self):
        self.assertEqual(setup_pc2.supported('ID=ubuntu\nVERSION_ID="20.04"','aarch64')['ID'],'ubuntu')
        for data,arch in (('ID=ubuntu\nVERSION_ID=22.04','aarch64'),('ID=debian\nVERSION_ID=20.04','aarch64'),
                          ('ID=ubuntu\nVERSION_ID=20.04','x86_64')):
            with self.assertRaises(RuntimeError):setup_pc2.supported(data,arch)

    def test_selected_setup_paths_restored_without_executing_or_leaking_key(self):
        path=self.project/'run/config/omni.env'
        path.parent.mkdir(parents=True)
        path.write_text("CYCLONEDDS_HOME='/tmp/dds prefix'\nDASHSCOPE_API_KEY=ignored\nCONTROL_IFACE=eth0\n")
        path.chmod(0o600)
        with patch.object(setup_pc2,'PROJECT',self.project), patch.dict(os.environ,{},clear=True):
            setup_pc2.load_selected_config()
            self.assertEqual(os.environ['CYCLONEDDS_HOME'],'/tmp/dds prefix')
            self.assertEqual(os.environ['CONTROL_IFACE'],'eth0')
            self.assertNotIn('DASHSCOPE_API_KEY',os.environ)
        path.write_text('CYCLONEDDS_HOME=$(command)\n')
        with patch.object(setup_pc2,'PROJECT',self.project):
            with self.assertRaises(RuntimeError):setup_pc2.load_selected_config()

    def test_prepare_default_is_motion_only_without_optional_model_install(self):
        python=self.project/'control/bin/python'
        python.parent.mkdir(parents=True)
        python.touch()
        services={key:[] for key in ('navigation','safe_controller','unsafe_controller','foreign_controller','foreign_navigation')}
        with patch.object(setup_pc2,'PROJECT',self.project), patch.object(setup_pc2,'supported'), \
                patch.object(setup_pc2,'control_python',return_value=python), \
                patch('initialize_robot_services.find_services',return_value=services), \
                patch.object(setup_pc2,'run') as run, \
                patch.object(setup_pc2.subprocess,'run',return_value=Mock(returncode=0,stdout='version')), \
                patch.object(setup_pc2.subprocess,'check_output',return_value='{}'):
            setup_pc2.prepare()
        commands=[call.args[0] for call in run.call_args_list]
        self.assertFalse(any('omni-requirements' in str(command) for command in commands))
        self.assertTrue(any('apt-get' in command for command in commands))
        config=self.project/'run/config/omni.env'
        self.assertEqual(config.stat().st_mode&0o777,0o600)

    def test_prepare_refuses_live_controller_before_any_install(self):
        services={key:[] for key in ('navigation','safe_controller','unsafe_controller','foreign_controller','foreign_navigation')}
        services['safe_controller']=[{'pid':123}]
        with patch.object(setup_pc2,'supported'), \
                patch('initialize_robot_services.find_services',return_value=services), patch.object(setup_pc2,'run') as run:
            with self.assertRaises(RuntimeError):setup_pc2.prepare()
            run.assert_not_called()


if __name__=='__main__':unittest.main()
