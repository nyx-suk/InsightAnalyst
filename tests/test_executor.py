
import unittest

from executor import execute_query
from contracts import QueryResult


class TestSQLExecutor(unittest.TestCase):

    # Test 1: A valid SELECT query should work.
    def test_select_query(self):
        result = execute_query(
            "Q1",
            "SELECT Country, Region FROM dim_country_region LIMIT 5;"
        )

        self.assertIsInstance(result, QueryResult)
        self.assertIsNone(result.error)
        self.assertEqual(result.row_count, 5)
        self.assertEqual(result.columns, ["Country", "Region"])

    # Test 2: The executor should return the correct country count.
    def test_country_count(self):
        result = execute_query(
            "Q2",
            "SELECT COUNT(*) AS total_countries FROM dim_country_region;"
        )

        self.assertIsNone(result.error)
        self.assertEqual(result.rows, [(21,)])

    # Test 3: INSERT must be blocked.
    def test_insert_blocked(self):
        result = execute_query(
            "Q3",
            "INSERT INTO dim_country_region VALUES ('TestCountry', 'South');"
        )

        self.assertIsNotNone(result.error)

    # Test 4: UPDATE must be blocked.
    def test_update_blocked(self):
        result = execute_query(
            "Q4",
            "UPDATE dim_country_region SET Region = 'North' WHERE Country = 'Argentina';"
        )

        self.assertIsNotNone(result.error)

    # Test 5: DELETE must be blocked.
    def test_delete_blocked(self):
        result = execute_query(
            "Q5",
            "DELETE FROM dim_country_region;"
        )

        self.assertIsNotNone(result.error)

    # Test 6: DROP must be blocked.
    def test_drop_blocked(self):
        result = execute_query(
            "Q6",
            "DROP TABLE dim_country_region;"
        )

        self.assertIsNotNone(result.error)

    # Test 7: CREATE must be blocked.
    def test_create_blocked(self):
        result = execute_query(
            "Q7",
            "CREATE TABLE test_table (id INTEGER);"
        )

        self.assertIsNotNone(result.error)

    # Test 8: ALTER must be blocked.
    def test_alter_blocked(self):
        result = execute_query(
            "Q8",
            "ALTER TABLE dim_country_region ADD COLUMN test_column TEXT;"
        )

        self.assertIsNotNone(result.error)

    # Test 9: ATTACH must be blocked.
    def test_attach_blocked(self):
        result = execute_query(
            "Q9",
            "ATTACH DATABASE ':memory:' AS testdb;"
        )

        self.assertIsNotNone(result.error)

    # Test 10: DETACH must be blocked.
    def test_detach_blocked(self):
        result = execute_query(
            "Q10",
            "DETACH DATABASE main;"
        )

        self.assertIsNotNone(result.error)

    # Test 11: PRAGMA must be blocked by the authorizer.
    def test_pragma_blocked(self):
        result = execute_query(
            "Q11",
            "PRAGMA user_version;"
        )

        self.assertIsNotNone(result.error)

    # Test 12: Multiple SQL statements must be rejected.
    def test_multiple_statements_blocked(self):
        result = execute_query(
            "Q12",
            "SELECT * FROM dim_country_region; DELETE FROM dim_country_region;"
        )

        self.assertIsNotNone(result.error)

    # Test 13: An invalid table should return an error.
    def test_invalid_table(self):
        result = execute_query(
            "Q13",
            "SELECT * FROM table_that_does_not_exist;"
        )

        self.assertIsNotNone(result.error)

    # Test 14: An empty query should return an error.
    def test_empty_query(self):
        result = execute_query("Q14", "")

        self.assertIsNotNone(result.error)

    # Test 15: Blocked operations must not change the database.
    def test_database_unchanged_after_blocked_operations(self):

        before = execute_query(
            "Q15A",
            "SELECT COUNT(*) FROM dim_country_region;"
        )

        execute_query(
            "Q15B",
            "INSERT INTO dim_country_region VALUES ('TestCountry', 'South');"
        )

        execute_query(
            "Q15C",
            "DELETE FROM dim_country_region;"
        )

        after = execute_query(
            "Q15D",
            "SELECT COUNT(*) FROM dim_country_region;"
        )

        self.assertIsNone(before.error)
        self.assertIsNone(after.error)
        self.assertEqual(before.rows, after.rows)
        self.assertEqual(after.rows, [(21,)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
