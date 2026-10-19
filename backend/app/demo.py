"""Demo datasets: a legacy RDBMS (SQLite, Oracle-style naming) migrated to a
Spark-style partitioned Parquet lake, with realistic migration defects."""

from __future__ import annotations

import datetime as dt
import random
import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

CITIES = ["Chennai", "Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Pune", "Kolkata", "Singapore", "London", "Dubai"]
STATUSES = ["NEW", "PAID", "SHIPPED", "DELIVERED", "CANCELLED"]


def _customers(rng: random.Random, n: int = 1000):
    base = dt.datetime(2021, 1, 1)
    rows = []
    for i in range(1, n + 1):
        rows.append(
            {
                "CUSTOMER_ID": i,
                "CUSTOMER_NAME": f"Customer {i:04d}",
                "EMAIL": f"customer{i}@example.com",
                "CITY": rng.choice(CITIES),
                "CREDIT_LIMIT": Decimal(rng.randrange(1000, 500000)) / 100,
                "CREATED_AT": base + dt.timedelta(minutes=rng.randrange(0, 60 * 24 * 900)),
            }
        )
    return rows


def _orders(rng: random.Random, n: int = 5000):
    base = dt.date(2024, 1, 1)
    rows = []
    for i in range(1, n + 1):
        rows.append(
            {
                "ORDER_ID": 100000 + i,
                "CUSTOMER_ID": rng.randrange(1, 1001),
                "ORDER_DATE": base + dt.timedelta(days=rng.randrange(0, 180)),
                "STATUS": rng.choice(STATUSES) + ("  " if rng.random() < 0.1 else ""),  # CHAR padding
                "AMOUNT": Decimal(rng.randrange(100, 2_000_000)) / 100,
                "QUANTITY": rng.randrange(1, 20),
            }
        )
    return rows


def _products(n: int = 200):
    return [
        {"PRODUCT_ID": i, "SKU": f"SKU-{i:05d}", "PRICE": Decimal(i * 137 % 10000) / 100, "ACTIVE": i % 7 != 0}
        for i in range(1, n + 1)
    ]


def _part(lake: Path, table: str) -> Path:
    (lake / table).mkdir(parents=True, exist_ok=True)
    return lake / table / "part-00000.parquet"


def generate(root: Path, force: bool = False) -> Path:
    demo = root / "demo"
    marker = demo / ".generated"
    if marker.exists() and not force:
        return demo
    if demo.exists():
        shutil.rmtree(demo)
    demo.mkdir(parents=True)
    rng = random.Random(42)
    customers, orders, products = _customers(rng), _orders(rng), _products()

    # ---- Source: legacy database --------------------------------------------------
    db = sqlite3.connect(demo / "legacy_oracle.db")
    db.executescript("""
        CREATE TABLE CUSTOMERS (CUSTOMER_ID INTEGER PRIMARY KEY, CUSTOMER_NAME VARCHAR(100) NOT NULL,
            EMAIL VARCHAR(255), CITY VARCHAR(50), CREDIT_LIMIT NUMERIC(12,2), CREATED_AT DATETIME);
        CREATE TABLE ORDERS (ORDER_ID INTEGER PRIMARY KEY, CUSTOMER_ID INTEGER NOT NULL, ORDER_DATE DATE,
            STATUS CHAR(12), AMOUNT NUMERIC(12,2), QUANTITY INTEGER);
        CREATE TABLE PRODUCTS (PRODUCT_ID INTEGER PRIMARY KEY, SKU VARCHAR(20), PRICE NUMERIC(10,2), ACTIVE BOOLEAN);
    """)
    db.executemany(
        "INSERT INTO CUSTOMERS VALUES (?,?,?,?,?,?)",
        [
            (
                r["CUSTOMER_ID"],
                r["CUSTOMER_NAME"],
                r["EMAIL"],
                r["CITY"],
                str(r["CREDIT_LIMIT"]),
                r["CREATED_AT"].isoformat(sep=" "),
            )
            for r in customers
        ],
    )
    db.executemany(
        "INSERT INTO ORDERS VALUES (?,?,?,?,?,?)",
        [
            (r["ORDER_ID"], r["CUSTOMER_ID"], r["ORDER_DATE"].isoformat(), r["STATUS"], str(r["AMOUNT"]), r["QUANTITY"])
            for r in orders
        ],
    )
    db.executemany(
        "INSERT INTO PRODUCTS VALUES (?,?,?,?)",
        [(r["PRODUCT_ID"], r["SKU"], str(r["PRICE"]), int(r["ACTIVE"])) for r in products],
    )
    db.commit()
    db.close()

    # ---- Target: Spark-style Parquet lake with migration defects ---------------------
    lake = demo / "spark_lake"
    lake.mkdir()

    # customers: 3 rows lost, 2 duplicated-in from another load, emails upper-cased, city drift
    t_customers = [dict(r) for r in customers if r["CUSTOMER_ID"] not in (17, 512, 901)]
    for r in t_customers:
        if r["CUSTOMER_ID"] % 197 == 0:
            r["EMAIL"] = r["EMAIL"].upper()
        if r["CUSTOMER_ID"] in (25, 250, 333, 777):
            r["CITY"] = None
    t_customers += [
        {
            "CUSTOMER_ID": 1001,
            "CUSTOMER_NAME": "Customer 1001",
            "EMAIL": "customer1001@example.com",
            "CITY": "Chennai",
            "CREDIT_LIMIT": Decimal("100.00"),
            "CREATED_AT": dt.datetime(2024, 5, 1),
        },
        {
            "CUSTOMER_ID": 1002,
            "CUSTOMER_NAME": "Customer 1002",
            "EMAIL": "bad-email",
            "CITY": "Pune",
            "CREDIT_LIMIT": Decimal("250.00"),
            "CREATED_AT": dt.datetime(2024, 5, 2),
        },
    ]
    pq.write_table(
        pa.table(
            {
                "customer_id": pa.array([r["CUSTOMER_ID"] for r in t_customers], pa.int64()),
                "customer_name": pa.array([r["CUSTOMER_NAME"] for r in t_customers], pa.string()),
                "email": pa.array([r["EMAIL"] for r in t_customers], pa.string()),
                "city": pa.array([r["CITY"] for r in t_customers], pa.string()),
                "credit_limit": pa.array([r["CREDIT_LIMIT"] for r in t_customers], pa.decimal128(12, 2)),
                "created_at": pa.array([r["CREATED_AT"] for r in t_customers], pa.timestamp("us", tz="UTC")),
                "load_ts": pa.array([dt.datetime(2024, 7, 1)] * len(t_customers), pa.timestamp("us")),
            }
        ),
        _part(lake, "customers"),
    )

    # orders: amount stored as decimal(10,1) (rounding), a few negative amounts, partitioned by month
    t_orders = [dict(r) for r in orders]
    for r in t_orders:
        r["AMOUNT"] = r["AMOUNT"].quantize(Decimal("0.1"))
        if r["ORDER_ID"] % 1250 == 0:
            r["AMOUNT"] = -r["AMOUNT"]
        r["STATUS"] = r["STATUS"].strip()
    orders_tbl = pa.table(
        {
            "order_id": pa.array([r["ORDER_ID"] for r in t_orders], pa.int64()),
            "customer_id": pa.array([r["CUSTOMER_ID"] for r in t_orders], pa.int32()),
            "order_date": pa.array([r["ORDER_DATE"] for r in t_orders], pa.date32()),
            "status": pa.array([r["STATUS"] for r in t_orders], pa.string()),
            "amount": pa.array([r["AMOUNT"] for r in t_orders], pa.decimal128(10, 1)),
            "quantity": pa.array([r["QUANTITY"] for r in t_orders], pa.int32()),
            "order_month": pa.array([r["ORDER_DATE"].strftime("%Y-%m") for r in t_orders], pa.string()),
        }
    )
    ds.write_dataset(
        orders_tbl, lake / "orders", format="parquet", partitioning=["order_month"], partitioning_flavor="hive"
    )

    # products: perfect copy
    pq.write_table(
        pa.table(
            {
                "product_id": pa.array([r["PRODUCT_ID"] for r in products], pa.int64()),
                "sku": pa.array([r["SKU"] for r in products], pa.string()),
                "price": pa.array([r["PRICE"] for r in products], pa.decimal128(10, 2)),
                "active": pa.array([r["ACTIVE"] for r in products], pa.bool_()),
            }
        ),
        _part(lake, "products"),
    )

    marker.write_text(dt.datetime.now().isoformat())
    return demo
