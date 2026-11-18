
import unittest

from executor import executor_fn
from validator import validator_fn


class TestRevenueReconciliation(unittest.TestCase):

    def test_monthly_revenue(self):

        sql = """
        SELECT
            strftime('%Y-%m', o.OrderDate) AS OrderMonth,
            ROUND(
                SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)),
                2
            ) AS Revenue
        FROM [Order Details] od
        JOIN Orders o
            ON od.OrderID = o.OrderID
        JOIN Customers c
            ON o.CustomerID = c.CustomerID
        JOIN dim_country_region dcr
            ON c.Country = dcr.Country
        WHERE dcr.Region = 'South'
          AND o.OrderDate >= '2023-08-01'
          AND o.OrderDate <= '2023-09-30'
        GROUP BY OrderMonth
        ORDER BY OrderMonth;
        """

        result = executor_fn("Q_REVENUE", sql)

        # Verify SQL execution.
        self.assertIsNone(result.error)

        # Verify expected columns.
        self.assertEqual(
            result.columns,
            ["OrderMonth", "Revenue"]
        )

        # Verify both months are returned.
        self.assertEqual(len(result.rows), 2)

        revenues = {
            row[0]: row[1]
            for row in result.rows
        }

        august = revenues["2023-08"]
        september = revenues["2023-09"]

        # Verify expected values from the canonical query.
        self.assertAlmostEqual(august, 687457.30, places=2)
        self.assertAlmostEqual(september, 634607.27, places=2)

        # Calculate the difference.
        difference = round(august - september, 2)

        percentage_change = round(
            ((september - august) / august) * 100,
            2
        )

        print("\n===== REVENUE RECONCILIATION =====")
        print(f"August Revenue:    {august:,.2f}")
        print(f"September Revenue: {september:,.2f}")
        print(f"Revenue Difference: {difference:,.2f}")
        print(f"Percentage Change: {percentage_change}%")

        # Validate the returned results.
        validations = validator_fn(
            result,
            {
                "expected_columns": ["OrderMonth", "Revenue"],
                "date_window": {
                    "start": "2023-08-01",
                    "end": "2023-09-30"
                },
                "date_column": "OrderMonth",
                "required_sql_fragments": [
                    "od.UnitPrice * od.Quantity * (1 - od.Discount)"
                ]
            }
        )

        for validation in validations:
            print(validation.to_dict())

        self.assertTrue(
            all(v.status == "pass" for v in validations),
            "One or more revenue validation checks failed."
        )


if __name__ == "__main__":
    unittest.main()
