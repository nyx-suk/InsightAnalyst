# Northwind Database Schema Notes & Quirks

These notes document the Northwind SQLite schema, join paths, and known quirks. They are embedded into ChromaDB to ground query planning and prevent common text-to-SQL pitfalls.

---

## 1. Core Tables & Join Relationships

### `Orders`
- **Primary Key**: `OrderID`
- **Foreign Keys**: `CustomerID -> Customers(CustomerID)`, `EmployeeID -> Employees(EmployeeID)`
- **Key Columns**:
  - `OrderDate`: Date string (ISO format `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`). Used for time filtering.
  - `ShipCountry`: Destination country.
  - `Freight`: Shipping fee. **CRITICAL**: Freight is a shipping charge, NEVER product revenue. Do not include Freight in revenue calculations.

### `[Order Details]`
- **Table Name Quirk**: The table name contains a space. In SQLite, it MUST ALWAYS be bracketed as `[Order Details]` or quoted as `"Order Details"`.
- **Primary Key**: `(OrderID, ProductID)`
- **Foreign Keys**: `OrderID -> Orders(OrderID)`, `ProductID -> Products(ProductID)`
- **Key Columns**:
  - `UnitPrice`: Unit price at time of sale.
  - `Quantity`: Number of units purchased.
  - `Discount`: Discount rate (0.0 to 1.0, e.g., 0.15 for 15% discount).

### `Products`
- **Primary Key**: `ProductID`
- **Foreign Keys**: `CategoryID -> Categories(CategoryID)`, `SupplierID -> Suppliers(SupplierID)`
- **Key Columns**: `ProductName`, `CategoryID`, `UnitPrice`, `Discontinued`.

### `Categories`
- **Primary Key**: `CategoryID`
- **Key Columns**: `CategoryName` (e.g., Beverages, Condiments, Confections, Dairy Products, Grains/Cereals, Meat/Poultry, Produce, Seafood), `Description`.

### `Customers`
- **Primary Key**: `CustomerID`
- **Key Columns**: `CompanyName`, `Country`, `City`, `Region`.
- **Note**: The `Country` column maps to customer sales regions via `dim_country_region`.

### `Employees`
- **Primary Key**: `EmployeeID`
- **Key Columns**: `LastName`, `FirstName`, `Title`.

---

## 2. Critical Join Rules & Anti-Patterns

### Anti-Pattern: Joining `Region` or `EmployeeTerritories`
- In raw Northwind, the `Region` table connects to employees via `EmployeeTerritories`.
- An employee can have multiple territories in multiple regions, causing Cartesian join fan-out that duplicates order lines and severely inflates revenue numbers.
- **Rule**: NEVER join `EmployeeTerritories` or `Region` to calculate customer regional sales.
- **Correct Path**: Use Person B's `dim_country_region` mapping table:
  ```sql
  JOIN Customers c ON o.CustomerID = c.CustomerID
  JOIN dim_country_region dcr ON c.Country = dcr.Country
  ```

---

## 3. Pending Profile Facts from Person B
- TODO(Person B): Confirm exact date range (`MIN(OrderDate)` to `MAX(OrderDate)`) and last complete month once data profiling is completed.
- TODO(Person B): Confirm exact table schema details and list of customer countries populated in `dim_country_region`.
