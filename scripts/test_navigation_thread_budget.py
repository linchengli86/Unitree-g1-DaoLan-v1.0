"""No-ROS tests for process-scoped numerical worker limits."""

import importlib
import ast
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import navigation_thread_budget as budget


class NavigationThreadBudgetTests(unittest.TestCase):
    def test_child_environment_overrides_existing_thread_counts(self):
        original = {name: "8" for name in budget.THREAD_BUDGET_VARIABLES}
        result = budget.child_env(original)
        self.assertEqual(result, {name: "1" for name in budget.THREAD_BUDGET_VARIABLES})
        self.assertEqual(original, {name: "8" for name in budget.THREAD_BUDGET_VARIABLES})
        self.assertIsNot(result, original)

    def test_child_environment_preserves_unrelated_ros_settings(self):
        original = {"ROS_MASTER_URI": "http://localhost:11311",
                    "PYTHONPATH": "/opt/ros/noetic/lib/python3/dist-packages",
                    "TASK_TEST_VALUE": "unchanged"}
        result = budget.child_env(original)
        for name, value in original.items():
            self.assertEqual(result[name], value)
        self.assertEqual(len(result), len(original) + len(budget.THREAD_BUDGET_VARIABLES))

    def test_child_environment_defaults_to_parent_without_mutating_it(self):
        parent = {"TASK_TEST_VALUE": "unchanged", "OPENBLAS_NUM_THREADS": "32"}
        with patch.object(budget.os, "environ", parent):
            result = budget.child_env()
        self.assertEqual(parent, {"TASK_TEST_VALUE": "unchanged", "OPENBLAS_NUM_THREADS": "32"})
        self.assertEqual(result["OPENBLAS_NUM_THREADS"], "1")

    def test_explicit_apply_changes_only_worker_limits(self):
        parent = {"TASK_TEST_VALUE": "unchanged", "OPENBLAS_NUM_THREADS": "8"}
        with patch.object(budget.os, "environ", parent):
            applied = budget.apply_current_process()
        self.assertEqual(parent["TASK_TEST_VALUE"], "unchanged")
        self.assertEqual(applied, {name: "1" for name in budget.THREAD_BUDGET_VARIABLES})
        self.assertEqual(parent, {"TASK_TEST_VALUE": "unchanged", **applied})

    def test_import_alone_does_not_apply_limits(self):
        parent = {"OPENBLAS_NUM_THREADS": "8", "TASK_TEST_VALUE": "unchanged"}
        with patch.object(budget.os, "environ", parent):
            importlib.reload(budget)
            self.assertEqual(parent, {"OPENBLAS_NUM_THREADS": "8", "TASK_TEST_VALUE": "unchanged"})

    def test_private_child_starts_with_limits_without_changing_parent(self):
        parent = dict(budget.os.environ)
        environment = budget.child_env()
        command = [sys.executable, "-c", "import os; print(os.environ['OPENBLAS_NUM_THREADS'])"]
        completed = subprocess.run(command, env=environment, capture_output=True,
                                   text=True, check=True, timeout=3)
        self.assertEqual(completed.stdout.strip(), "1")
        self.assertEqual(dict(budget.os.environ), parent)

    def test_helper_budget_precedes_ros_or_numpy_imports(self):
        root = Path(__file__).resolve().parent
        entries = (("navigate_to_point_safe.py", "RosBackend", "__init__"),
                   ("capture_guide_pose.py", None, "capture"),
                   ("web_relocalize.py", None, "relocalize"),
                   ("diagnose_navigation_latency.py", None, "run_ros"))
        for filename, cls, name in entries:
            with self.subTest(filename=filename):
                tree = ast.parse((root / filename).read_text())
                scope = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                             and node.name == cls).body if cls else tree.body
                function = next(node for node in scope if isinstance(node, ast.FunctionDef)
                                and node.name == name)
                application = next(node.lineno for node in ast.walk(function)
                                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                                   and node.func.id == "apply_current_process")
                numerical_imports = [node.lineno for node in ast.walk(function)
                                     if isinstance(node, (ast.Import, ast.ImportFrom))
                                     and any(name.startswith(("rospy", "actionlib", "tf", "numpy"))
                                             for name in ([alias.name for alias in node.names]
                                                          if isinstance(node, ast.Import) else [node.module or ""]))]
                self.assertTrue(numerical_imports)
                self.assertLess(application, min(numerical_imports))

    def test_web_helper_spawn_has_private_budget_and_initializer_does_not_apply_it(self):
        root = Path(__file__).resolve().parent
        tree = ast.parse((root / "guide_points.py").read_text())
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "run_ros_json")
        spawns = [node for node in ast.walk(helper) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"]
        self.assertEqual(len(spawns), 1)
        private = next(keyword.value for keyword in spawns[0].keywords if keyword.arg == "env")
        self.assertIsInstance(private, ast.Call)
        self.assertEqual(private.func.id, "child_env")
        initializer = (root / "initialize_robot_services.py").read_text()
        self.assertNotIn("apply_current_process", initializer)
        self.assertNotIn("navigation_thread_budget", initializer)


if __name__ == "__main__":
    unittest.main()
