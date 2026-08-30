import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).with_name("contrast-v2.py")


class ContrastV2ConsoleEncodingTests(unittest.TestCase):
    def test_gate_runs_under_gbk_console(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], cwd=ROOT,
            env={**os.environ, "PYTHONIOENCODING": "gbk"},
            capture_output=True, text=True, encoding="gbk", errors="strict",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("门禁 50 组 / FAIL 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
