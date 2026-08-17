import unittest
from pathlib import Path


class DockerRuntimeContractTests(unittest.TestCase):
    def test_image_installs_pinned_requirements_and_documented_tooling(self):
        dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("COPY requirements.txt /app/requirements.txt", dockerfile)
        self.assertIn("pip install --no-cache-dir -r /app/requirements.txt", dockerfile)
        self.assertNotIn("pip install --no-cache-dir flask", dockerfile)
        self.assertIn("COPY kcd /app/kcd", dockerfile)
        self.assertIn("COPY tools /app/tools", dockerfile)


if __name__ == "__main__":
    unittest.main()
