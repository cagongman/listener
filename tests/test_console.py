import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from listener.console_process import spawn_console


@unittest.skipUnless(os.name == "nt", "Windows console handles")
class ConsoleTests(unittest.TestCase):
    def test_child_gets_console_stdio_even_when_parent_output_is_redirected(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "handles.json"
            code = ("import json,os,sys; from pathlib import Path; "
                    "Path(sys.argv[1]).write_text(json.dumps([os.isatty(0),os.isatty(1),os.isatty(2)]))")
            process = spawn_console([sys.executable, "-c", code, str(report)], cwd=folder,
                                    env=dict(os.environ), hidden=True)
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertEqual(process.poll(), 0)
            self.assertEqual(json.loads(report.read_text()), [True, True, True])
