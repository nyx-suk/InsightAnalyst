import csv
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path


DB_PATH = Path(os.environ.get("NORTHWIND_DB", "data/northwind.db"))
REGION_MAP = {
    "USA": "North",
    "Canada": "North",
    "Mexico": "North",
    "Brazil": "South",
    "Argentina": "South",
    "Venezuela": "South",
    "UK": "Europe",
    "Ireland": "Europe",
    "France": "Europe",
    "Germany": "Europe",
    "Spain": "Europe",
    "Italy": "Europe",
    "Portugal": "Europe",
    "Belgium": "Europe",
    "Switzerland": "Europe",
    "Austria": "Europe",
    "Denmark": "Europe",
    "Finland": "Europe",
    "Sweden": "Europe",
    "Norway": "Europe",
    "Poland": "Europe",
    "Netherlands": "Europe",
}


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def find_table(cur: sqlite3.Cursor, candidates: tuple[str, ...]) -> str | None:
    tables = {
        row[0].casefold(): row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    for candidate in candidates:
        if candidate.casefold() in tables:
            return tables[candidate.casefold()]
    return None


def table_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    return [
        row[1]
        for row in cur.execute(f"PRAGMA table_info({quote_ident(table)})")
    ]


def find_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lookup = {column.casefold(): column for column in columns}
    for candidate in candidates:
        if candidate.casefold() in lookup:
            return lookup[candidate.casefold()]
    return None


def parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None
    for value_format in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text, value_format).date()
        except ValueError:
            pass
    return None


def month_label(value: date | None) -> str:
    return value.strftime("%Y-%m") if value else ""


def infer_last_complete_month(latest: date | None) -> date | None:
    if latest is None:
        return None

    if latest.month == 12:
        first_next_month = date(latest.year + 1, 1, 1)
    else:
        first_next_month = date(latest.year, latest.month + 1, 1)

    if latest == first_next_month.fromordinal(first_next_month.toordinal() - 1):
        return date(latest.year, latest.month, 1)
    if latest.month == 1:
        return date(latest.year - 1, 12, 1)
    return date(latest.year, latest.month - 1, 1)


def write_schema(cur: sqlite3.Cursor, tables: list[str]) -> None:
    statements = []
    for table in tables:
        row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        statements.append(row[0] if row and row[0] else f"-- No CREATE statement for {table}")

        statements.append("")
        statements.append(f"-- Columns for {table}")
        for column in cur.execute(f"PRAGMA table_info({quote_ident(table)})"):
            cid, name, data_type, not_null, default, pk = column
            statements.append(
                f"-- {cid}: {name} | {data_type or 'NONE'} | "
                f"NOT NULL={bool(not_null)} | DEFAULT={default} | PK={bool(pk)}"
            )
        statements.append("")

    Path("schema.sql").write_text("\n".join(statements), encoding="utf-8")


def main() -> None:
    db_path = DB_PATH
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    if not db_path.exists():
        raise SystemExit(
            f"Northwind database not found: {db_path}\n"
            "Set NORTHWIND_DB or place it at data/northwind.db."
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tables = [
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    write_schema(cur, tables)

    orders_table = find_table(cur, ("Orders",))
    customers_table = find_table(cur, ("Customers",))
    details_table = find_table(cur, ("Order Details", "OrderDetails", "Order_Details"))

    orders_columns = table_columns(cur, orders_table) if orders_table else []
    customers_columns = table_columns(cur, customers_table) if customers_table else []

    date_col = find_column(orders_columns, ("OrderDate", "Order Date", "Date")) if orders_columns else None
    earliest = None
    latest = None
    invalid_dates = 0
    date_format_note = "No order date column found"

    if orders_table and date_col:
        date_q = quote_ident(date_col)
        raw_dates = [row[0] for row in cur.execute(f"SELECT {date_q} FROM {quote_ident(orders_table)}")]
        parsed_dates = []
        for raw in raw_dates:
            parsed = parse_date(raw)
            if parsed:
                parsed_dates.append(parsed)
            elif raw not in (None, ""):
                invalid_dates += 1

        if parsed_dates:
            earliest = min(parsed_dates)
            latest = max(parsed_dates)
            date_format_note = "Dates parsed as ISO, US, or common text formats"

    last_complete = infer_last_complete_month(latest)

    country_col = find_column(customers_columns, ("Country", "CountryName")) if customers_columns else None
    countries = []
    if customers_table and country_col:
        country_q = quote_ident(country_col)
        countries = [
            row[0]
            for row in cur.execute(
                f"SELECT DISTINCT {country_q} FROM {quote_ident(customers_table)} "
                f"WHERE {country_q} IS NOT NULL AND TRIM(CAST({country_q} AS TEXT)) <> '' "
                f"ORDER BY {country_q}"
            )
            if row[0] is not None
        ]

    with Path("country_region_template.csv").open("w", newline="", encoding="utf-8") as country_file:
        writer = csv.writer(country_file)
        writer.writerow(["country", "proposed_sales_region", "confirmed_sales_region"])
        for country in countries:
            writer.writerow([country, REGION_MAP.get(str(country), ""), ""])

    quirks = []
    if orders_table:
        for col in ("OrderDate", "OrderDate", "CustomerID", "EmployeeID"):
            actual = find_column(orders_columns, (col,))
            if actual:
                qcol = quote_ident(actual)
                qtable = quote_ident(orders_table)
                null_count = cur.execute(
                    f"SELECT COUNT(*) FROM {qtable} WHERE {qcol} IS NULL"
                ).fetchone()[0]
                blank_count = cur.execute(
                    f"SELECT COUNT(*) FROM {qtable} "
                    f"WHERE typeof({qcol}) = 'text' AND TRIM({qcol}) = ''"
                ).fetchone()[0]
                if null_count or blank_count:
                    quirks.append(
                        f"- {orders_table}.{actual}: {null_count} NULL value(s), "
                        f"{blank_count} blank text value(s)"
                    )

    if invalid_dates:
        quirks.append(
            f"- {orders_table}.{date_col}: {invalid_dates} non-null date value(s) "
            "could not be parsed by this script"
        )

    if details_table:
        detail_columns = table_columns(cur, details_table)
        lower_columns = {column.lower(): column for column in detail_columns}
        detail_q = quote_ident(details_table)

        for candidate in ("quantity", "discount", "unitprice"):
            actual = lower_columns.get(candidate)
            if actual:
                q = quote_ident(actual)
                count = cur.execute(
                    f"SELECT COUNT(*) FROM {detail_q} WHERE CAST({q} AS REAL) < 0"
                ).fetchone()[0]
                if count:
                    quirks.append(f"- {details_table}.{actual}: {count} negative value(s)")

        discount_col = lower_columns.get("discount")
        if discount_col:
            q = quote_ident(discount_col)
            count = cur.execute(
                f"SELECT COUNT(*) FROM {detail_q} "
                f"WHERE CAST({q} AS REAL) < 0 OR CAST({q} AS REAL) > 1"
            ).fetchone()[0]
            if count:
                quirks.append(
                    f"- {details_table}.{discount_col}: {count} value(s) outside [0, 1]"
                )

    lines = [
        "# Person B → Person A: Dataset facts",
        "",
        f"Database file: `{db_path}`",
        "",
        "## 1. Database schema",
        "See the generated `schema.sql` file for CREATE statements and column metadata.",
        "",
        "Tables found:",
    ]
    lines += [f"- {table}" for table in tables] or ["- No user tables found"]
    lines += [
        "",
        "## 2. Order date range and format",
        f"- Date column: `{orders_table}.{date_col}`" if orders_table and date_col else "- Date column: NOT FOUND",
        f"- Earliest order date: `{earliest.isoformat() if earliest else 'NOT FOUND'}`",
        f"- Latest order date: `{latest.isoformat() if latest else 'NOT FOUND'}`",
        f"- {date_format_note}",
        "",
        "## 3. Last complete month",
        f"- Inferred last complete month: `{month_label(last_complete) if last_complete else 'NOT FOUND'}`",
        "- Rule used: if the latest order date is before the final calendar day of its month, "
        "the prior month is treated as the latest complete month; otherwise the latest "
        "month is treated as complete.",
        "- Confirm this assumption against the dataset's intended coverage before using it "
        "as a business rule.",
        "",
        "## 4. Customer countries and region mapping",
        f"- Customer table: `{customers_table or 'NOT FOUND'}`",
        "- Exact country list and proposed mappings are in `country_region_template.csv`.",
        "- Review the proposed mapping with Person A, fill `confirmed_sales_region`, "
        "and investigate any blank proposed mappings.",
        "",
        "Countries found:",
    ]
    lines += [f"- {country}" for country in countries] or ["- No country values found"]
    lines += ["", "## 5. Odd values and quirks"]
    lines += quirks or ["- No NULL/blank/date/negative-value issues detected by the checks in this script."]
    lines += [
        "",
        "## Important metric note",
        "For Northwind order details, the project's agreed revenue formula is "
        "`UnitPrice * Quantity * (1 - Discount)`. Do not include Freight in revenue.",
        "",
        "## Files generated",
        "- `person_a_facts.md`",
        "- `schema.sql`",
        "- `country_region_template.csv`",
    ]

    Path("person_a_facts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    conn.close()

    print("\nInspection complete.")
    print(f"Database: {db_path}")
    print("Created: person_a_facts.md")
    print("Created: schema.sql")
    print("Created: country_region_template.csv")


if __name__ == "__main__":
    main()
