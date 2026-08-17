import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SqlOnlyValidatorTests(unittest.TestCase):
    def test_validator_uses_isolated_ready_state(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "tools" / "validate_sql_only.py"
        env = os.environ.copy()
        env.pop("KCD_SQL_ONLY_FAIL_FAST", None)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=tmpdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("validation OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
