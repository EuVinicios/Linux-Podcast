import os
import subprocess
import sys
import unittest
from pathlib import Path

import tests  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                     "precisa de um display (use xvfb-run)")
class UiSmokeTest(unittest.TestCase):
    def test_every_screen_opens(self):
        env = dict(os.environ)
        env.setdefault("GSK_RENDERER", "cairo")
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "screenshots.py"), "--smoke"],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output[-4000:])
        self.assertNotIn("Traceback", output)
        self.assertNotIn("-CRITICAL", output, output[-4000:])
        self.assertIn("step: atalhos", output)


if __name__ == "__main__":
    unittest.main()
