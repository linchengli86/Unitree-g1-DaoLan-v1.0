"""Worker ownership and map serialization checks without importing ROS."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import web_mapping_worker as worker


class WorkerTests(unittest.TestCase):
    def test_stop_refuses_reused_pid_without_sending_signal(self):
        original = {'pid': 123, 'argv': ['owned'], 'cwd': '/tmp', 'uid': 1000, 'start_ticks': 5}
        with patch.object(worker.init, 'process_record', return_value={**original, 'start_ticks': 6}), \
                patch.object(worker.os, 'kill') as kill:
            with self.assertRaises(RuntimeError): worker.stop_owned(original)
            kill.assert_not_called()

    def test_stop_only_signals_exact_live_process(self):
        original = {'pid': 123, 'argv': ['owned'], 'cwd': '/tmp', 'uid': 1000, 'start_ticks': 5}
        with patch.object(worker.init, 'process_record', side_effect=[original, None]), \
                patch.object(worker.os, 'kill') as kill:
            worker.stop_owned(original)
            kill.assert_called_once_with(123, worker.signal.SIGINT)

    def test_offline_rejects_mapping_or_navigation_node(self):
        for name in ('/move_base', '/map_builder_node', '/unitree_safe_controller'):
            with patch.object(worker, 'inventory', return_value={'nodes': [name]}):
                with self.assertRaises(RuntimeError): worker.offline_check()

    def message(self):
        return NS(header=NS(frame_id='map', stamp=NS(to_sec=lambda: 99.8)), data=[0,100,-1,0],
            info=NS(width=2, height=2, resolution=.05,
                origin=NS(position=NS(x=1,y=2,z=0),orientation=NS(x=0,y=0,z=0,w=1))))

    def helper(self, message):
        rospy=Mock()
        rospy.wait_for_message.return_value=message
        rospy.Time.now.return_value.to_sec.return_value=100
        return rospy

    def test_grid_is_flipped_and_yaml_image_is_relative(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(worker,'ros_helper',return_value=self.helper(self.message())), \
                patch.dict('sys.modules',{'nav_msgs.msg':NS(OccupancyGrid=object)}):
            root=Path(temporary)
            worker.save_grid(root)
            self.assertEqual((root/'map/map.pgm').read_bytes(), b'P5\n2 2\n255\n'+bytes([205,254,254,0]))
            self.assertIn('image: map.pgm', (root/'map/map.yaml').read_text())
            self.assertIn('origin: [1, 2, 0.0]', (root/'map/map.yaml').read_text())

    def test_wrong_frame_stale_empty_and_nonplanar_grid_rejected(self):
        for kind in ('frame', 'stale', 'empty', 'quaternion'):
            message=self.message()
            if kind=='frame':message.header.frame_id='local'
            if kind=='stale':message.header.stamp.to_sec=lambda:90
            if kind=='empty':message.data=[-1]*4
            if kind=='quaternion':message.info.origin.orientation.w=0
            with tempfile.TemporaryDirectory() as temporary, \
                    patch.object(worker,'ros_helper',return_value=self.helper(message)), \
                    patch.dict('sys.modules',{'nav_msgs.msg':NS(OccupancyGrid=object)}):
                with self.assertRaises(ValueError):worker.save_grid(Path(temporary))
                self.assertFalse((Path(temporary)/'map').exists())


if __name__=='__main__': unittest.main()
