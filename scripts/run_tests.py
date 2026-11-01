"""Run Study Buddy unit tests and explicitly report GUI tests skipped without Tkinter."""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
sys.path.insert(0, APP)

GUI_MODULES = {
    "test_dashboard_architecture",
    "test_camera_preview",
    "test_camera_chat_permissions",
    "test_v9_chat_isolation",
}
HAS_TK = importlib.util.find_spec("tkinter") is not None

loader = unittest.TestLoader()
suite = unittest.TestSuite()
skipped = []
for filename in sorted(os.listdir(APP)):
    if not filename.startswith("test_") or not filename.endswith(".py"):
        continue
    module_name = filename[:-3]
    if not HAS_TK and module_name in GUI_MODULES:
        skipped.append((module_name, "tkinter unavailable in this environment"))
        continue
    suite.addTests(loader.loadTestsFromName(module_name))

result = unittest.TextTestRunner(verbosity=2).run(suite)
print("\nTEST SUMMARY")
print(f"ran={result.testsRun}")
print(f"passed={result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)}")
print(f"skipped={len(result.skipped) + len(skipped)}")
for name, reason in skipped:
    print(f"SKIPPED {name}: {reason}")
print(f"failed={len(result.failures) + len(result.errors)}")
sys.exit(0 if result.wasSuccessful() else 1)
