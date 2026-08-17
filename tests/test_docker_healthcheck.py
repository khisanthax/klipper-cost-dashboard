import unittest
from pathlib import Path


class DockerHealthcheckTests(unittest.TestCase):
    def test_compose_healthcheck_uses_readiness_endpoint(self):
        compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:5000/health", compose)
        self.assertIn("interval: 30s", compose)
        self.assertIn("timeout: 5s", compose)
        self.assertIn("retries: 3", compose)
        self.assertIn("start_period: 20s", compose)


if __name__ == "__main__":
    unittest.main()
