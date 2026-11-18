CREATE TABLE [Categories]
(      [CategoryID] INTEGER PRIMARY KEY AUTOINCREMENT,
       [CategoryName] TEXT,
       [Description] TEXT,
       [Picture] BLOB
)

-- Columns for Categories
-- 0: CategoryID | INTEGER | NOT NULL=False | DEFAULT=None | PK=True
-- 1: CategoryName | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 2: Description | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 3: Picture | BLOB | NOT NULL=False | DEFAULT=None | PK=False

CREATE TABLE [CustomerCustomerDemo](
   [CustomerID]TEXT NOT NULL,
   [CustomerTypeID]TEXT NOT NULL,
   PRIMARY KEY ("CustomerID","CustomerTypeID"),
   FOREIGN KEY ([CustomerID]) REFERENCES [Customers] ([CustomerID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION,
	FOREIGN KEY ([CustomerTypeID]) REFERENCES [CustomerDemographics] ([CustomerTypeID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION
)

-- Columns for CustomerCustomerDemo
-- 0: CustomerID | TEXT | NOT NULL=True | DEFAULT=None | PK=True
-- 1: CustomerTypeID | TEXT | NOT NULL=True | DEFAULT=None | PK=True

CREATE TABLE [CustomerDemographics](
   [CustomerTypeID]TEXT NOT NULL,
   [CustomerDesc]TEXT,
    PRIMARY KEY ("CustomerTypeID")
)

-- Columns for CustomerDemographics
-- 0: CustomerTypeID | TEXT | NOT NULL=True | DEFAULT=None | PK=True
-- 1: CustomerDesc | TEXT | NOT NULL=False | DEFAULT=None | PK=False

CREATE TABLE [Customers]
(      [CustomerID] TEXT,
       [CompanyName] TEXT,
       [ContactName] TEXT,
       [ContactTitle] TEXT,
       [Address] TEXT,
       [City] TEXT,
       [Region] TEXT,
       [PostalCode] TEXT,
       [Country] TEXT,
       [Phone] TEXT,
       [Fax] TEXT,
       PRIMARY KEY (`CustomerID`)
)

-- Columns for Customers
-- 0: CustomerID | TEXT | NOT NULL=False | DEFAULT=None | PK=True
-- 1: CompanyName | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 2: ContactName | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 3: ContactTitle | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 4: Address | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 5: City | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 6: Region | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 7: PostalCode | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 8: Country | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 9: Phone | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 10: Fax | TEXT | NOT NULL=False | DEFAULT=None | PK=False

CREATE TABLE [EmployeeTerritories](
   [EmployeeID]INTEGER NOT NULL,
   [TerritoryID]TEXT NOT NULL,
    PRIMARY KEY ("EmployeeID","TerritoryID"),
	FOREIGN KEY ([EmployeeID]) REFERENCES [Employees] ([EmployeeID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION,
	FOREIGN KEY ([TerritoryID]) REFERENCES [Territories] ([TerritoryID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION
)

-- Columns for EmployeeTerritories
-- 0: EmployeeID | INTEGER | NOT NULL=True | DEFAULT=None | PK=True
-- 1: TerritoryID | TEXT | NOT NULL=True | DEFAULT=None | PK=True

CREATE TABLE [Employees]
(      [EmployeeID] INTEGER PRIMARY KEY AUTOINCREMENT,
       [LastName] TEXT,
       [FirstName] TEXT,
       [Title] TEXT,
       [TitleOfCourtesy] TEXT,
       [BirthDate] DATE,
       [HireDate] DATE,
       [Address] TEXT,
       [City] TEXT,
       [Region] TEXT,
       [PostalCode] TEXT,
       [Country] TEXT,
       [HomePhone] TEXT,
       [Extension] TEXT,
       [Photo] BLOB,
       [Notes] TEXT,
       [ReportsTo] INTEGER,
       [PhotoPath] TEXT,
	   FOREIGN KEY ([ReportsTo]) REFERENCES [Employees] ([EmployeeID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION
)

-- Columns for Employees
-- 0: EmployeeID | INTEGER | NOT NULL=False | DEFAULT=None | PK=True
-- 1: LastName | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 2: FirstName | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 3: Title | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 4: TitleOfCourtesy | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 5: BirthDate | DATE | NOT NULL=False | DEFAULT=None | PK=False
-- 6: HireDate | DATE | NOT NULL=False | DEFAULT=None | PK=False
-- 7: Address | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 8: City | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 9: Region | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 10: PostalCode | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 11: Country | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 12: HomePhone | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 13: Extension | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 14: Photo | BLOB | NOT NULL=False | DEFAULT=None | PK=False
-- 15: Notes | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 16: ReportsTo | INTEGER | NOT NULL=False | DEFAULT=None | PK=False
-- 17: PhotoPath | TEXT | NOT NULL=False | DEFAULT=None | PK=False

CREATE TABLE [Order Details](
   [OrderID]INTEGER NOT NULL,
   [ProductID]INTEGER NOT NULL,
   [UnitPrice]NUMERIC NOT NULL DEFAULT 0,
   [Quantity]INTEGER NOT NULL DEFAULT 1,
   [Discount]REAL NOT NULL DEFAULT 0,
    PRIMARY KEY ("OrderID","ProductID"),
    CHECK ([Discount]>=(0) AND [Discount]<=(1)),
    CHECK ([Quantity]>(0)),
    CHECK ([UnitPrice]>=(0)),
	FOREIGN KEY ([OrderID]) REFERENCES [Orders] ([OrderID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION,
	FOREIGN KEY ([ProductID]) REFERENCES [Products] ([ProductID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION
)

-- Columns for Order Details
-- 0: OrderID | INTEGER | NOT NULL=True | DEFAULT=None | PK=True
-- 1: ProductID | INTEGER | NOT NULL=True | DEFAULT=None | PK=True
-- 2: UnitPrice | NUMERIC | NOT NULL=True | DEFAULT=0 | PK=False
-- 3: Quantity | INTEGER | NOT NULL=True | DEFAULT=1 | PK=False
-- 4: Discount | REAL | NOT NULL=True | DEFAULT=0 | PK=False

CREATE TABLE [Orders](
   [OrderID]INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
   [CustomerID]TEXT,
   [EmployeeID]INTEGER,
   [OrderDate]DATETIME,
   [RequiredDate]DATETIME,
   [ShippedDate]DATETIME,
   [ShipVia]INTEGER,
   [Freight]NUMERIC DEFAULT 0,
   [ShipName]TEXT,
   [ShipAddress]TEXT,
   [ShipCity]TEXT,
   [ShipRegion]TEXT,
   [ShipPostalCode]TEXT,
   [ShipCountry]TEXT,
   FOREIGN KEY ([EmployeeID]) REFERENCES [Employees] ([EmployeeID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION,
	FOREIGN KEY ([CustomerID]) REFERENCES [Customers] ([CustomerID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION,
	FOREIGN KEY ([ShipVia]) REFERENCES [Shippers] ([ShipperID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION
)

-- Columns for Orders
-- 0: OrderID | INTEGER | NOT NULL=True | DEFAULT=None | PK=True
-- 1: CustomerID | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 2: EmployeeID | INTEGER | NOT NULL=False | DEFAULT=None | PK=False
-- 3: OrderDate | DATETIME | NOT NULL=False | DEFAULT=None | PK=False
-- 4: RequiredDate | DATETIME | NOT NULL=False | DEFAULT=None | PK=False
-- 5: ShippedDate | DATETIME | NOT NULL=False | DEFAULT=None | PK=False
-- 6: ShipVia | INTEGER | NOT NULL=False | DEFAULT=None | PK=False
-- 7: Freight | NUMERIC | NOT NULL=False | DEFAULT=0 | PK=False
-- 8: ShipName | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 9: ShipAddress | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 10: ShipCity | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 11: ShipRegion | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 12: ShipPostalCode | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 13: ShipCountry | TEXT | NOT NULL=False | DEFAULT=None | PK=False

CREATE TABLE [Products](
   [ProductID]INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
   [ProductName]TEXT NOT NULL,
   [SupplierID]INTEGER,
   [CategoryID]INTEGER,
   [QuantityPerUnit]TEXT,
   [UnitPrice]NUMERIC DEFAULT 0,
   [UnitsInStock]INTEGER DEFAULT 0,
   [UnitsOnOrder]INTEGER DEFAULT 0,
   [ReorderLevel]INTEGER DEFAULT 0,
   [Discontinued]TEXT NOT NULL DEFAULT '0',
    CHECK ([UnitPrice]>=(0)),
    CHECK ([ReorderLevel]>=(0)),
    CHECK ([UnitsInStock]>=(0)),
    CHECK ([UnitsOnOrder]>=(0)),
	FOREIGN KEY ([CategoryID]) REFERENCES [Categories] ([CategoryID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION,
	FOREIGN KEY ([SupplierID]) REFERENCES [Suppliers] ([SupplierID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION
)

-- Columns for Products
-- 0: ProductID | INTEGER | NOT NULL=True | DEFAULT=None | PK=True
-- 1: ProductName | TEXT | NOT NULL=True | DEFAULT=None | PK=False
-- 2: SupplierID | INTEGER | NOT NULL=False | DEFAULT=None | PK=False
-- 3: CategoryID | INTEGER | NOT NULL=False | DEFAULT=None | PK=False
-- 4: QuantityPerUnit | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 5: UnitPrice | NUMERIC | NOT NULL=False | DEFAULT=0 | PK=False
-- 6: UnitsInStock | INTEGER | NOT NULL=False | DEFAULT=0 | PK=False
-- 7: UnitsOnOrder | INTEGER | NOT NULL=False | DEFAULT=0 | PK=False
-- 8: ReorderLevel | INTEGER | NOT NULL=False | DEFAULT=0 | PK=False
-- 9: Discontinued | TEXT | NOT NULL=True | DEFAULT='0' | PK=False

CREATE TABLE [Regions](
   [RegionID]INTEGER NOT NULL PRIMARY KEY,
   [RegionDescription]TEXT NOT NULL
)

-- Columns for Regions
-- 0: RegionID | INTEGER | NOT NULL=True | DEFAULT=None | PK=True
-- 1: RegionDescription | TEXT | NOT NULL=True | DEFAULT=None | PK=False

CREATE TABLE [Shippers](
   [ShipperID]INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
   [CompanyName]TEXT NOT NULL,
   [Phone]TEXT
)

-- Columns for Shippers
-- 0: ShipperID | INTEGER | NOT NULL=True | DEFAULT=None | PK=True
-- 1: CompanyName | TEXT | NOT NULL=True | DEFAULT=None | PK=False
-- 2: Phone | TEXT | NOT NULL=False | DEFAULT=None | PK=False

CREATE TABLE [Suppliers](
   [SupplierID]INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
   [CompanyName]TEXT NOT NULL,
   [ContactName]TEXT,
   [ContactTitle]TEXT,
   [Address]TEXT,
   [City]TEXT,
   [Region]TEXT,
   [PostalCode]TEXT,
   [Country]TEXT,
   [Phone]TEXT,
   [Fax]TEXT,
   [HomePage]TEXT
)

-- Columns for Suppliers
-- 0: SupplierID | INTEGER | NOT NULL=True | DEFAULT=None | PK=True
-- 1: CompanyName | TEXT | NOT NULL=True | DEFAULT=None | PK=False
-- 2: ContactName | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 3: ContactTitle | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 4: Address | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 5: City | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 6: Region | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 7: PostalCode | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 8: Country | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 9: Phone | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 10: Fax | TEXT | NOT NULL=False | DEFAULT=None | PK=False
-- 11: HomePage | TEXT | NOT NULL=False | DEFAULT=None | PK=False

CREATE TABLE [Territories](
   [TerritoryID]TEXT NOT NULL,
   [TerritoryDescription]TEXT NOT NULL,
   [RegionID]INTEGER NOT NULL,
    PRIMARY KEY ("TerritoryID"),
	FOREIGN KEY ([RegionID]) REFERENCES [Regions] ([RegionID]) 
		ON DELETE NO ACTION ON UPDATE NO ACTION
)

-- Columns for Territories
-- 0: TerritoryID | TEXT | NOT NULL=True | DEFAULT=None | PK=True
-- 1: TerritoryDescription | TEXT | NOT NULL=True | DEFAULT=None | PK=False
-- 2: RegionID | INTEGER | NOT NULL=True | DEFAULT=None | PK=False
