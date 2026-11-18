
import unittest

from executor import execute_query
from validator import validate
from contracts import ValidationResult


class TestValidator(unittest.TestCase):

    def test_successful_query(self):
        result = execute_query(
            "Q1",
            "SELECT COUNT(*) AS total FROM dim_country_region;"
        )

        checks = validate(result)

        self.assertTrue(all(
            isinstance(check, ValidationResult)
            for check in checks
        ))

        self.assertEqual(checks[0].status, "pass")

    def test_failed_query(self):
        result = execute_query(
            "Q2",
            "SELECT * FROM invalid_table;"
        )

        checks = validate(result)

        self.assertEqual(checks[0].status, "fail")
        self.assertEqual(checks[0].check_name, "execution")

    def test_expected_columns(self):
        result = execute_query(
            "Q3",
            "SELECT Country, Region FROM dim_country_region LIMIT 5;"
        )

        checks = validate(
            result,
            {
                "expected_columns": ["Country", "Region"]
            }
        )

        column_check = next(
            c for c in checks if c.check_name == "expected_columns"
        )

        self.assertEqual(column_check.status, "pass")

    def test_missing_column(self):
        result = execute_query(
            "Q4",
            "SELECT Country FROM dim_country_region LIMIT 5;"
        )

        checks = validate(
            result,
            {
                "expected_columns": ["Country", "Region"]
            }
        )

        column_check = next(
            c for c in checks if c.check_name == "expected_columns"
        )

        self.assertEqual(column_check.status, "fail")

    def test_reconciliation_pass(self):
        result = execute_query(
            "Q5",
            "SELECT COUNT(*) AS total FROM dim_country_region;"
        )

        checks = validate(
            result,
            {
                "reconciliation": {
                    "total": 21,
                    "breakdown_total": 21
                }
            }
        )

        reconciliation_check = next(
            c for c in checks if c.check_name == "reconciliation"
        )

        self.assertEqual(reconciliation_check.status, "pass")

    def test_reconciliation_fail(self):
        result = execute_query(
            "Q6",
            "SELECT COUNT(*) AS total FROM dim_country_region;"
        )

        checks = validate(
            result,
            {
                "reconciliation": {
                    "total": 21,
                    "breakdown_total": 20
                }
            }
        )

        reconciliation_check = next(
            c for c in checks if c.check_name == "reconciliation"
        )

        self.assertEqual(reconciliation_check.status, "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
