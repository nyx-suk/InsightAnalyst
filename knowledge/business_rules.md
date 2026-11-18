# Business Rules & Query Guidelines

These business rules define how InsightAnalyst interprets temporal phrasing, regional hierarchies, and safety constraints.

---

## Rule 1: Time Anchoring ("Last Month" & "Previous Period")
- **Definition**: Historical databases like Northwind end in 1998. "Last month" is NEVER interpreted relative to today's real-world calendar date.
- **Rule**: The agent must anchor "last month" to the **last complete calendar month** recorded in the Northwind database.
- **Previous Period**: "Previous period" or "prior month" strictly refers to the month immediately preceding the anchored month.
- **Assumption Statement**: The final report must explicitly state this assumption (e.g., *"Anchored to April 1998 as the last complete month in the dataset"*).
- TODO(Person B): Fill in the exact dataset date range (`MIN(OrderDate)`, `MAX(OrderDate)`) and the designated last complete month once profiling completes.

---

## Rule 2: Region Mapping & Fan-out Prevention
- **Definition**: Customer sales regions are determined by mapping customer billing/shipping countries to geographical sales regions (e.g., North America, Western Europe, South America).
- **Rule**: Regional calculations must use Person B's `dim_country_region` table:
  ```sql
  JOIN Customers c ON o.CustomerID = c.CustomerID
  JOIN dim_country_region dcr ON c.Country = dcr.Country
  ```
- **Integrity Check**: Region breakdown totals must sum up to the total company metric (reconciliation check).
- TODO(Person B): Provide the canonical list of regions and mapped countries from `dim_country_region`.

---

## Rule 3: Read-Only Query Constraints
- **Rule**: All queries must strictly be single `SELECT` statements.
- **Forbidden**: Any statement containing `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, `DETACH`, `PRAGMA`, or multiple statements joined by semicolons is strictly prohibited.
- If a user asks to modify the database (e.g., "Delete all orders from 1997"), the planner must immediately refuse the request.
