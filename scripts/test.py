"""Run all CPU-only checks with no model imports or Docker/GPU execution."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / part) for part in ("portal", "scripts", "guard", "tests")]
suite = unittest.TestSuite()
for name in (
    "test_resource_guard",
    "test_duration",
    "test_portal",
    "test_long_video",
    "test_lifecycle",
):
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
