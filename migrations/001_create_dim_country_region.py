
import csv
import sqlite3
from pathlib import Path


# Locate project files automatically
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "northwind.db"
CSV_PATH = PROJECT_ROOT / "country_region_template.csv"

ALLOWED_REGIONS = {"South", "North", "Europe"}


def load_country_mapping():
    """Read and validate the approved country-region mapping."""

    mapping = {}

    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        required_columns = {
            "country",
            "proposed_sales_region",
            "confirmed_sales_region"
        }

        if not required_columns.issubset(reader.fieldnames or []):
            raise ValueError("CSV is missing required columns.")

        for row in reader:
            country = row["country"].strip()

            # Use confirmed value if filled; otherwise use
            # the proposed value already approved by Person A.
            region = (
                row["confirmed_sales_region"].strip()
                or row["proposed_sales_region"].strip()
            )

            if not country or not region:
                raise ValueError(f"Missing country or region: {row}")

            if region not in ALLOWED_REGIONS:
                raise ValueError(
                    f"Invalid region '{region}' for {country}"
                )

            if country in mapping:
                raise ValueError(f"Duplicate country: {country}")

            mapping[country] = region

    if len(mapping) != 21:
        raise ValueError(
            f"Expected 21 countries, but found {len(mapping)}."
        )

    return mapping


def create_dimension_table():
    """Create and populate dim_country_region safely."""

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")

    mapping = load_country_mapping()

    connection = sqlite3.connect(DB_PATH)

    try:
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS dim_country_region (
                    Country TEXT PRIMARY KEY,
                    Region TEXT NOT NULL
                        CHECK (Region IN ('South', 'North', 'Europe'))
                )
            """)

            connection.executemany("""
                INSERT INTO dim_country_region (Country, Region)
                VALUES (?, ?)
                ON CONFLICT(Country) DO NOTHING
            """, list(mapping.items()))

            actual = dict(connection.execute("""
                SELECT Country, Region
                FROM dim_country_region
            """).fetchall())

            if actual != mapping:
                raise ValueError(
                    "Database mapping does not match the approved CSV. "
                    "No changes were committed."
                )

        print("Migration completed successfully!")
        print(f"Countries loaded and verified: {len(mapping)}")
        print("\nRegion counts:")

        for region, count in connection.execute("""
            SELECT Region, COUNT(*)
            FROM dim_country_region
            GROUP BY Region
            ORDER BY Region
        """):
            print(f"{region}: {count}")

    finally:
        connection.close()


if __name__ == "__main__":
    create_dimension_table()
