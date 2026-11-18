
"""
validator.py

Person B: Validates SQL query results before they are
used to generate analytical findings.
"""

from datetime import date
from contracts import QueryResult, ValidationResult


def validate(result: QueryResult, context: dict = None) -> list[ValidationResult]:
    """
    Validate an executed SQL query.

    Args:
        result: QueryResult returned by executor.py.
        context: Optional dictionary containing validation requirements.

    Returns:
        A list of ValidationResult objects.
    """

    if context is None:
        context = {}

    validations = []

    def add(status, reason, check_name):
        validations.append(
            ValidationResult(
                status=status,
                reason=reason,
                check_name=check_name
            )
        )

    # 1. Execution check
    if result.error:
        add(
            "fail",
            f"SQL execution failed: {result.error}",
            "execution"
        )
        return validations

    add(
        "pass",
        "SQL query executed successfully.",
        "execution"
    )

    # 2. Empty result check
    if not result.rows:
        add(
            "suspicious",
            "Query returned no rows. Check the filters and date range.",
            "empty_result"
        )
    else:
        add(
            "pass",
            f"Query returned {len(result.rows)} rows.",
            "empty_result"
        )

    # 3. Expected columns check (only when specified)
    expected_columns = context.get("expected_columns")

    if expected_columns is not None:
        missing = [
            column for column in expected_columns
            if column not in result.columns
        ]

        if missing:
            add(
                "fail",
                f"Missing expected columns: {missing}",
                "expected_columns"
            )
        else:
            add(
                "pass",
                "All expected columns are present.",
                "expected_columns"
            )

    # 4. Null value check
    if context.get("check_nulls", True) and result.rows:
        null_count = sum(
            value is None
            for row in result.rows
            for value in row
        )

        if null_count:
            add(
                "suspicious",
                f"Found {null_count} NULL values in the query results.",
                "null_values"
            )
        else:
            add(
                "pass",
                "No NULL values found in the query results.",
                "null_values"
            )

    # 5. Date window check (only when configured)
    date_window = context.get("date_window")

    if date_window:
        date_column = context.get("date_column")

        if not date_column or date_column not in result.columns:
            add(
                "fail",
                "A valid date_column must be supplied for date validation.",
                "date_window"
            )
        else:
            column_index = result.columns.index(date_column)
            start = date.fromisoformat(date_window["start"])
            end = date.fromisoformat(date_window["end"])

            invalid_dates = []

            for row in result.rows:
                value = row[column_index]

                try:
                    # Accept YYYY-MM as well as YYYY-MM-DD.
                    if isinstance(value, date):
                        parsed_date = value
                    elif isinstance(value, str) and len(value) == 7:
                        parsed_date = date.fromisoformat(value + "-01")
                    else:
                        parsed_date = date.fromisoformat(str(value)[:10])

                    if not start <= parsed_date <= end:
                        invalid_dates.append(value)

                except (ValueError, TypeError):
                    invalid_dates.append(value)

            if invalid_dates:
                add(
                    "fail",
                    f"Dates outside the requested window or invalid: {invalid_dates}",
                    "date_window"
                )
            else:
                add(
                    "pass",
                    "All returned dates fall within the requested window.",
                    "date_window"
                )

    # 6. Metric definition check (only when SQL fragments are supplied)
    required_fragments = context.get("required_sql_fragments", [])

    if required_fragments:
        normalized_sql = " ".join(result.sql.lower().split())

        missing_fragments = [
            fragment
            for fragment in required_fragments
            if " ".join(fragment.lower().split()) not in normalized_sql
        ]

        if missing_fragments:
            add(
                "fail",
                f"Required SQL fragments are missing: {missing_fragments}",
                "metric_definition"
            )
        else:
            add(
                "pass",
                "Required SQL fragments are present.",
                "metric_definition"
            )

    # 7. Reconciliation check (only when configured)
    reconciliation = context.get("reconciliation")

    if reconciliation:
        total = reconciliation.get("total")
        breakdown_total = reconciliation.get("breakdown_total")
        tolerance = reconciliation.get("tolerance", 0.01)

        if total is None or breakdown_total is None:
            add(
                "fail",
                "Reconciliation requires both total and breakdown_total.",
                "reconciliation"
            )
        else:
            try:
                difference = abs(float(total) - float(breakdown_total))

                if difference <= tolerance:
                    add(
                        "pass",
                        f"Totals reconcile. Difference: {difference:.4f}",
                        "reconciliation"
                    )
                else:
                    add(
                        "fail",
                        f"Totals do not reconcile. Difference: {difference:.4f}",
                        "reconciliation"
                    )

            except (ValueError, TypeError):
                add(
                    "fail",
                    "Reconciliation values must be numeric.",
                    "reconciliation"
                )

    return validations


# Interface for Person A's agent loop.
validator_fn = validate
