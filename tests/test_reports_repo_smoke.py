import unittest

from core import reports_repo


class ReportsRepoSmokeTests(unittest.TestCase):
    def test_reports_repo_functions_exist(self):
        self.assertTrue(hasattr(reports_repo, "get_reports_data"))
        self.assertTrue(hasattr(reports_repo, "get_reports_data_range"))


if __name__ == "__main__":
    unittest.main()