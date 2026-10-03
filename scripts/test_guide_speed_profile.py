"""Read-only config regression: operator-requested speed, unchanged safeguards."""

import ast
import unittest
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parent.parent


class GuideSpeedProfileTests(unittest.TestCase):
    def test_teb_speed_and_acceleration_profile(self):
        config = yaml.safe_load((PROJECT / 'G1Nav2D/src/movebase/param/teb_local_planner_params.yaml').read_text())['TebLocalPlannerROS']
        for key, expected in {'max_vel_x': .6, 'max_vel_theta': .7, 'acc_lim_x': .3,
                              'acc_lim_theta': .5, 'max_vel_x_backwards': .05, 'max_vel_y': 0.0}.items():
            self.assertEqual(config[key], expected, key)

    def test_safe_controller_defaults_and_disarmed_start(self):
        source = PROJECT / 'unitree_sdk2_python/example/g1/high_level/g1_control_safe.py'
        tree = ast.parse(source.read_text())
        defaults = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get_param' and len(node.args) > 1:
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[1], ast.Constant):
                    defaults[node.args[0].value] = node.args[1].value
        for key, expected in {'~max_vx': .6, '~max_wz': .7, '~max_vy': 0.0, '~watchdog_timeout': .3}.items():
            self.assertEqual(defaults[key], expected, key)
        init = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == '__init__')
        disarmed = [node for node in ast.walk(init) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Attribute) and target.attr == 'armed' for target in node.targets)]
        self.assertEqual(len(disarmed), 1)
        self.assertIs(disarmed[0].value.value, False)


if __name__ == '__main__':
    unittest.main()
