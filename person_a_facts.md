# Person B → Person A: Dataset facts

Database file: `/Users/hariniroopkumar/InsightAnalyst/data/northwind.db`

## 1. Database schema
See the generated `schema.sql` file for CREATE statements and column metadata.

Tables found:
- Categories
- CustomerCustomerDemo
- CustomerDemographics
- Customers
- EmployeeTerritories
- Employees
- Order Details
- Orders
- Products
- Regions
- Shippers
- Suppliers
- Territories

## 2. Order date range and format
- Date column: `Orders.OrderDate`
- Earliest order date: `2012-07-10`
- Latest order date: `2023-10-28`
- Dates parsed as ISO, US, or common text formats

## 3. Last complete month
- Inferred last complete month: `2023-09`
- Rule used: if the latest order date is before the final calendar day of its month, the prior month is treated as the latest complete month; otherwise the latest month is treated as complete.
- Confirm this assumption against the dataset's intended coverage before using it as a business rule.

## 4. Customer countries and region mapping
- Customer table: `Customers`
- Exact country list and proposed mappings are in `country_region_template.csv`.
- Review the proposed mapping with Person A, fill `confirmed_sales_region`, and investigate any blank proposed mappings.

Countries found:
- Argentina
- Austria
- Belgium
- Brazil
- Canada
- Denmark
- Finland
- France
- Germany
- Ireland
- Italy
- Mexico
- Norway
- Poland
- Portugal
- Spain
- Sweden
- Switzerland
- UK
- USA
- Venezuela

## 5. Odd values and quirks
- No NULL/blank/date/negative-value issues detected by the checks in this script.

## Important metric note
For Northwind order details, the project's agreed revenue formula is `UnitPrice * Quantity * (1 - Discount)`. Do not include Freight in revenue.

## Files generated
- `person_a_facts.md`
- `schema.sql`
- `country_region_template.csv`
