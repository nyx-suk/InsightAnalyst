# Northwind Database Schema Notes & Quirks

## Categories
- **Purpose**: Product category classifications.
- **Key Columns**:
  - `CategoryID` (INTEGER, PK, AUTOINCREMENT): Unique category identifier.
  - `CategoryName` (TEXT): Category name (e.g., Beverages, Condiments, Confections, Dairy Products).
  - `Description` (TEXT): Category description.
  - `Picture` (BLOB): Binary image data.
- **Joins**:
  - `Products.CategoryID -> Categories.CategoryID`
- **Gotchas**: `Picture` is a binary BLOB; never include `Picture` in analytical queries.
- **TO CONFIRM**: None (verified).

## CustomerCustomerDemo
- **Purpose**: Junction table linking customers to customer demographic types.
- **Key Columns**:
  - `CustomerID` (TEXT, PK, NOT NULL): Customer identifier.
  - `CustomerTypeID` (TEXT, PK, NOT NULL): Demographic type identifier.
- **Joins**:
  - `CustomerCustomerDemo.CustomerID -> Customers.CustomerID`
  - `CustomerCustomerDemo.CustomerTypeID -> CustomerDemographics.CustomerTypeID`
- **Gotchas**: Composite primary key `(CustomerID, CustomerTypeID)`.
- **TO CONFIRM**: Whether this table contains active business data in this database.

## CustomerDemographics
- **Purpose**: Customer segment and demographic profile descriptions.
- **Key Columns**:
  - `CustomerTypeID` (TEXT, PK, NOT NULL): Identifier code.
  - `CustomerDesc` (TEXT): Narrative demographic description.
- **Joins**:
  - Linked to customers via `CustomerCustomerDemo`.
- **Gotchas**: Rarely referenced in standard revenue or product performance questions.
- **TO CONFIRM**: Whether demographic categories are ever queried in business analytics.

## Customers
- **Purpose**: Client master records containing company identity, geography, and contacts.
- **Key Columns**:
  - `CustomerID` (TEXT, PK): 5-character string customer code (e.g., `ALFKI`).
  - `CompanyName` (TEXT): Legal customer business name.
  - `Country` (TEXT): Customer headquarters country (21 distinct countries confirmed).
  - `City` (TEXT): Customer billing city.
  - `Region` (TEXT): Administrative state or province subdivision.
- **Joins**:
  - `Orders.CustomerID -> Customers.CustomerID`
  - `Customers.Country -> dim_country_region.Country` (for sales region mapping)
- **Gotchas**: `Customers.Region` is frequently NULL or blank and is NOT the sales region. All regional sales reporting must join `dim_country_region` on `Customers.Country`.
- **TO CONFIRM**: Whether `Customers.Country` always matches `Orders.ShipCountry` across all historical orders.

## EmployeeTerritories
- **Purpose**: Junction table assigning employees to geographical sales territories.
- **Key Columns**:
  - `EmployeeID` (INTEGER, PK, NOT NULL): Staff identifier.
  - `TerritoryID` (TEXT, PK, NOT NULL): Territory code.
- **Joins**:
  - `EmployeeTerritories.EmployeeID -> Employees.EmployeeID`
  - `EmployeeTerritories.TerritoryID -> Territories.TerritoryID`
- **Gotchas**: **CRITICAL ANTI-PATTERN**: An employee can be assigned to multiple territories across multiple regions. Joining `Orders -> Employees -> EmployeeTerritories -> Territories -> Regions` creates massive Cartesian row duplication that severely inflates revenue. NEVER join through `EmployeeTerritories` for sales reporting.
- **TO CONFIRM**: Confirmed anti-pattern; regional sales queries must strictly use `dim_country_region`.

## Employees
- **Purpose**: Internal staff, sales representatives, and organizational management hierarchy.
- **Key Columns**:
  - `EmployeeID` (INTEGER, PK, AUTOINCREMENT): Staff identifier.
  - `LastName` (TEXT), `FirstName` (TEXT): Employee names.
  - `Title` (TEXT): Job role (e.g., "Sales Representative").
  - `ReportsTo` (INTEGER): Manager's EmployeeID (self-referencing FK).
  - `BirthDate` (DATE), `HireDate` (DATE): HR dates.
- **Joins**:
  - `Orders.EmployeeID -> Employees.EmployeeID`
  - `Employees.ReportsTo -> Employees.EmployeeID` (self-join)
- **Gotchas**: Contains `Photo` (BLOB); never include in `SELECT`.
- **TO CONFIRM**: Raw date formats for `BirthDate` and `HireDate` if ever queried.

## [Order Details]
- **Purpose**: Transactional line items recording quantity, unit price, and discount for each product in an order.
- **Key Columns**:
  - `OrderID` (INTEGER, PK, NOT NULL): Parent order reference.
  - `ProductID` (INTEGER, PK, NOT NULL): Sold product reference.
  - `UnitPrice` (NUMERIC, NOT NULL, DEFAULT 0, CHECK >= 0): Price charged per unit.
  - `Quantity` (INTEGER, NOT NULL, DEFAULT 1, CHECK > 0): Units purchased.
  - `Discount` (REAL, NOT NULL, DEFAULT 0, CHECK 0 <= Discount <= 1): Discount fraction.
- **Joins**:
  - `[Order Details].OrderID -> Orders.OrderID`
  - `[Order Details].ProductID -> Products.ProductID`
- **Gotchas**:
  - **TABLE NAME SPACE**: Must ALWAYS be quoted/bracketed as `[Order Details]` or `"Order Details"`.
  - **REVENUE FORMULA**: Line revenue is strictly: `UnitPrice * Quantity * (1 - Discount)`.
  - `Discount` is a float rate between 0.0 and 1.0 (e.g. 0.15 for 15%), NOT an integer percentage.
- **TO CONFIRM**: None (formula and column constraints verified).

## Orders
- **Purpose**: Order header transactions including customer, salesperson, dates, destination, and freight.
- **Key Columns**:
  - `OrderID` (INTEGER, PK, AUTOINCREMENT): Unique order number.
  - `CustomerID` (TEXT, FK): Purchasing client.
  - `EmployeeID` (INTEGER, FK): Sales rep handling the order.
  - `OrderDate` (DATETIME): Date order was placed (earliest: `2012-07-10`, latest: `2023-10-28`).
  - `Freight` (NUMERIC, DEFAULT 0): Shipping surcharge fee.
  - `ShipCountry` (TEXT): Destination shipping country.
  - `ShipVia` (INTEGER, FK to Shippers): Shipping carrier ID.
- **Joins**:
  - `Orders.OrderID -> [Order Details].OrderID`
  - `Orders.CustomerID -> Customers.CustomerID`
  - `Orders.EmployeeID -> Employees.EmployeeID`
  - `Orders.ShipVia -> Shippers.ShipperID`
- **Gotchas**:
  - **FREIGHT IS NOT REVENUE**: Freight is a shipping surcharge, never product revenue. Do not include Freight in revenue calculations.
  - Joining `Orders` with `[Order Details]` duplicates order-level rows. Always use `COUNT(DISTINCT o.OrderID)` when counting orders.
- **TO CONFIRM**: Raw string format of `OrderDate` in SQLite when writing comparisons vs `strftime()`.

## Products
- **Purpose**: Product catalog items, pricing, inventory levels, and active status.
- **Key Columns**:
  - `ProductID` (INTEGER, PK, AUTOINCREMENT): Product code.
  - `ProductName` (TEXT, NOT NULL): Product display name.
  - `CategoryID` (INTEGER, FK): Linked category.
  - `SupplierID` (INTEGER, FK): Linked manufacturer/vendor.
  - `UnitPrice` (NUMERIC, DEFAULT 0, CHECK >= 0): Current catalog list price.
  - `UnitsInStock` (INTEGER, DEFAULT 0), `UnitsOnOrder` (INTEGER, DEFAULT 0): Stock counts.
  - `Discontinued` (TEXT, NOT NULL, DEFAULT '0'): Discontinuation flag.
- **Joins**:
  - `[Order Details].ProductID -> Products.ProductID`
  - `Products.CategoryID -> Categories.CategoryID`
  - `Products.SupplierID -> Suppliers.SupplierID`
- **Gotchas**: `Discontinued` is declared as TEXT (`'0'` or `'1'`), not an integer or boolean. `Products.UnitPrice` is current catalog price, whereas `[Order Details].UnitPrice` is historical transaction price.
- **TO CONFIRM**: Whether `Discontinued` values ever appear as `'False'`/`'True'` in the database.

## Regions
- **Purpose**: High-level internal territory groupings for sales representatives.
- **Key Columns**:
  - `RegionID` (INTEGER, PK, NOT NULL): Region code.
  - `RegionDescription` (TEXT, NOT NULL): Text label (e.g., Eastern, Western, Northern, Southern).
- **Joins**:
  - `Territories.RegionID -> Regions.RegionID`
- **Gotchas**: Table name is plural: `Regions`. Reflects employee territories, NOT customer sales regions. Do not use for customer sales metrics.
- **TO CONFIRM**: None (verified).

## Shippers
- **Purpose**: Delivery companies used for fulfilling orders.
- **Key Columns**:
  - `ShipperID` (INTEGER, PK, AUTOINCREMENT): Carrier code.
  - `CompanyName` (TEXT, NOT NULL): Carrier brand (e.g., Speedy Express, United Package).
  - `Phone` (TEXT): Carrier contact phone.
- **Joins**:
  - `Orders.ShipVia -> Shippers.ShipperID`
- **Gotchas**: Foreign key in `Orders` is named `ShipVia`, NOT `ShipperID`.
- **TO CONFIRM**: None (verified).

## Suppliers
- **Purpose**: Product vendors and manufacturers.
- **Key Columns**:
  - `SupplierID` (INTEGER, PK, AUTOINCREMENT): Vendor code.
  - `CompanyName` (TEXT, NOT NULL): Vendor company name.
  - `Country` (TEXT), `City` (TEXT): Vendor location.
- **Joins**:
  - `Products.SupplierID -> Suppliers.SupplierID`
- **Gotchas**: `Suppliers.Country` is the manufacturer's location, NOT customer sales location. Do not join `Suppliers` for regional sales queries.
- **TO CONFIRM**: None (verified).

## Territories
- **Purpose**: Granular geographical territory codes assigned to employees.
- **Key Columns**:
  - `TerritoryID` (TEXT, PK, NOT NULL): Postal or alphanumeric territory code.
  - `TerritoryDescription` (TEXT, NOT NULL): Territory name.
  - `RegionID` (INTEGER, NOT NULL, FK): Parent region ID in `Regions`.
- **Joins**:
  - `Territories.RegionID -> Regions.RegionID`
  - `EmployeeTerritories.TerritoryID -> Territories.TerritoryID`
- **Gotchas**: `TerritoryID` is TEXT, not an integer. Used only for internal territory-to-employee mapping.
- **TO CONFIRM**: None (verified).
