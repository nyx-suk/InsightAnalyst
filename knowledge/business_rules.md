# Business Rules & Query Guidelines

These business rules define how InsightAnalyst interprets temporal phrasing, regional hierarchies, and safety constraints over the Northwind database.

---

## Rule 1: Time Anchoring ("Last Month" & "Previous Period")
- **Definition**: Historical database orders span `2012-07-10` through `2023-10-28`.
- **Anchor Rule**: "Last month" is anchored to **September 2023 (`2023-09-01` to `2023-09-30`)** because it is the latest complete calendar month in the dataset.
- **Previous Period**: "Previous period" or "prior month" refers strictly to **August 2023 (`2023-08-01` to `2023-08-31`)**.
- **Report Assumption**: Every generated report must explicitly disclose:
  > *"Analysis anchored to September 2023 as the last complete calendar month in the Northwind dataset; baseline comparison period is August 2023."*

---

## Rule 2: Region Mapping & Fan-Out Prevention
- **Definition**: Customer sales regions are determined by mapping customer countries to geographical sales regions via `dim_country_region`.
- **Verified Regions & Countries**:
  - **South**: Argentina, Brazil, Venezuela
  - **North**: Canada, Mexico, USA
  - **Europe**: Austria, Belgium, Denmark, Finland, France, Germany, Ireland, Italy, Norway, Poland, Portugal, Spain, Sweden, Switzerland, UK
- **Anti-Pattern Guard**: NEVER join through `EmployeeTerritories` or `Region`. Such joins multiply line items across assigned sales reps and inflate revenue figures.
- **Canonical Join Path**:
  ```sql
  JOIN Customers c ON o.CustomerID = c.CustomerID
  JOIN dim_country_region dcr ON c.Country = dcr.Country
  ```

---

## Rule 3: Read-Only Query Constraints
- **Rule**: All queries must strictly be single `SELECT` statements.
- **Forbidden**: Any statement containing `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, `DETACH`, `PRAGMA`, or multiple statements joined by semicolons is strictly prohibited.
- If a user asks to modify the database (e.g., "Delete all orders from 2023"), the planner must immediately refuse the request.
