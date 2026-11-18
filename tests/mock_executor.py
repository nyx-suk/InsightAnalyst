"""tests/mock_executor.py
Mock executor and validator implementing Person B's contract specifications.
Allows Person A to test planner.py and loop.py in isolation before integration.
"""

import sqlite3
from typing import List, Tuple
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import QueryResult, ValidationResult


class MockDatabase:
    """In-memory SQLite database simulating Northwind and dim_country_region."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self._init_schema()
        self._seed_data()

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.executescript("""
        CREATE TABLE Customers (
            CustomerID TEXT PRIMARY KEY,
            CompanyName TEXT,
            Country TEXT
        );

        CREATE TABLE dim_country_region (
            Country TEXT PRIMARY KEY,
            Region TEXT
        );

        CREATE TABLE Products (
            ProductID INTEGER PRIMARY KEY,
            ProductName TEXT,
            CategoryID INTEGER,
            UnitPrice REAL
        );

        CREATE TABLE Orders (
            OrderID INTEGER PRIMARY KEY,
            CustomerID TEXT,
            OrderDate TEXT,
            ShipCountry TEXT,
            Freight REAL
        );

        CREATE TABLE [Order Details] (
            OrderID INTEGER,
            ProductID INTEGER,
            UnitPrice REAL,
            Quantity INTEGER,
            Discount REAL,
            PRIMARY KEY (OrderID, ProductID)
        );
        """)
        self.conn.commit()

    def _seed_data(self):
        cur = self.conn.cursor()
        # Seed Customers & dim_country_region
        cur.executemany("INSERT INTO dim_country_region VALUES (?, ?)", [
            ("Brazil", "South"),
            ("Argentina", "South"),
            ("USA", "North America"),
            ("Germany", "Western Europe")
        ])
        cur.executemany("INSERT INTO Customers VALUES (?, ?, ?)", [
            ("CUST1", "Rio Foods", "Brazil"),
            ("CUST2", "Buenos Aires Market", "Argentina"),
            ("CUST3", "US Mart", "USA")
        ])
        # Seed Products
        cur.executemany("INSERT INTO Products VALUES (?, ?, ?, ?)", [
            (1, "Chai", 1, 18.0),
            (2, "Chang", 1, 19.0),
            (3, "Aniseed Syrup", 2, 10.0)
        ])
        # Seed Orders (Month 1: 1998-03, Month 2: 1998-04)
        cur.executemany("INSERT INTO Orders VALUES (?, ?, ?, ?, ?)", [
            (101, "CUST1", "1998-03-15", "Brazil", 50.0),
            (102, "CUST2", "1998-03-20", "Argentina", 30.0),
            (103, "CUST1", "1998-04-10", "Brazil", 40.0),
            (104, "CUST3", "1998-04-12", "USA", 25.0)
        ])
        # Seed Order Details (Sales dropped in South in 1998-04)
        cur.executemany("INSERT INTO [Order Details] VALUES (?, ?, ?, ?, ?)", [
            (101, 1, 18.0, 100, 0.0),  # Revenue = 1800
            (102, 2, 19.0, 50, 0.1),   # Revenue = 855
            (103, 1, 18.0, 20, 0.0),   # Revenue = 360 (Drop!)
            (104, 3, 10.0, 200, 0.0)   # Revenue = 2000
        ])
        self.conn.commit()


# Singleton mock DB instance
_db_instance = None


def get_mock_db() -> MockDatabase:
    global _db_instance
    if _db_instance is None:
        _db_instance = MockDatabase()
    return _db_instance


def mock_executor(query_id: str, sql: str) -> QueryResult:
    """Mock execution function returning a QueryResult conforming to contracts.py."""
    db = get_mock_db()
    cur = db.conn.cursor()

    # Safety check: Reject write keywords
    upper_sql = sql.upper()
    for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "PRAGMA"]:
        if f"{kw} " in upper_sql or f" {kw}" in upper_sql:
            return QueryResult(
                query_id=query_id,
                sql=sql,
                error=f"SECURITY_VIOLATION: Write operation '{kw}' is blocked."
            )

    try:
        cur.execute(sql)
        rows: List[Tuple] = cur.fetchall()
        columns = [d[0] for d in cur.description] if cur.description else []
        return QueryResult(
            query_id=query_id,
            sql=sql,
            columns=columns,
            rows=rows,
            row_count=len(rows)
        )
    except sqlite3.Error as e:
        return QueryResult(
            query_id=query_id,
            sql=sql,
            error=str(e)
        )


def mock_validator(result: QueryResult) -> List[ValidationResult]:
    """Mock validator implementing the primary sanity checks."""
    validations: List[ValidationResult] = []

    # Check 1: Execution error
    if result.error:
        validations.append(ValidationResult(
            status="fail",
            reason=result.error,
            check_name="execution_check"
        ))
        return validations

    # Check 2: Empty or null
    if result.row_count == 0:
        validations.append(ValidationResult(
            status="suspicious",
            reason="Query returned zero rows. Check date filters or country mapping.",
            check_name="empty_check"
        ))
    else:
        validations.append(ValidationResult(
            status="pass",
            check_name="empty_check"
        ))

    # Check 3: Definition check (no Freight in SQL)
    if "freight" in result.sql.lower():
        validations.append(ValidationResult(
            status="fail",
            reason="Freight was detected in query. Freight must never be part of revenue.",
            check_name="definition_check"
        ))
    else:
        validations.append(ValidationResult(
            status="pass",
            check_name="definition_check"
        ))

    return validations
