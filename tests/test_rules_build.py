"""The build must reject semantic failures even when Souffle exits successfully."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build


class BuildGate(unittest.TestCase):
    def test_semantic_failure_is_not_a_successful_build(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            Path('datalog').mkdir()
            Path('datalog/cards.dl').write_text(
                '.decl conformance_fail(reason: symbol)\n'
                'conformance_fail("missing rule").\n.output conformance_fail\n')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertFalse(build.compile_check())
            self.assertIn('CONFORMANCE FAIL', output.getvalue())

    def test_required_compiler_cannot_be_silently_skipped(self):
        with patch.object(build.shutil, 'which', return_value=None):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(build.compile_check())


if __name__ == '__main__':
    unittest.main()
