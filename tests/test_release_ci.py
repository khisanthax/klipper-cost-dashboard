import unittest
from pathlib import Path


class ReleaseValidationWorkflowTests(unittest.TestCase):
    def test_release_workflow_covers_runtime_and_container_contracts(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "release-validation.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("pull_request:", workflow)
        self.assertNotIn("paths:", workflow)
        self.assertIn("python -m pip install -r requirements.txt", workflow)
        self.assertIn("python -m unittest discover -s tests", workflow)
        self.assertIn("python -m compileall", workflow)
        self.assertIn("python tools/validate_sql_only.py", workflow)
        self.assertIn("docker build --tag kcd-release-ci .", workflow)
        self.assertIn("python -m kcd --help", workflow)
        self.assertIn("python -m kcd db readiness", workflow)
        self.assertIn("python tools/kcd_backup.py --help", workflow)
        self.assertIn("/app/tools/validate_sql_only.py", workflow)


if __name__ == "__main__":
    unittest.main()
