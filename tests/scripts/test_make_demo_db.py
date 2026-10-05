import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from make_demo_db import (
    CUSTOMERS,
    DESTINATION,
    FIRST_DAY,
    LAST_DAY,
    ORDERS,
    SEED,
    SIDECAR,
    DemoShopBuilder,
)
from text_to_sql_demo.execution import QueryExecutor
from text_to_sql_demo.registry import DatabaseRegistry
from text_to_sql_demo.safety import SqlValidator
from text_to_sql_demo.schema import SchemaInspector

TABLES = [
    "categories",
    "customers",
    "order_items",
    "orders",
    "products",
    "returns",
    "staff",
    "stores",
]

REVENUE_BY_MONTH = (
    "SELECT strftime('%Y-%m', o.ordered_at) AS month, "
    "round(sum(i.quantity * i.unit_price * (1 - i.discount)), 2) AS revenue "
    "FROM orders AS o JOIN order_items AS i ON i.order_id = o.order_id "
    "WHERE o.status = 'completed' GROUP BY month ORDER BY month"
)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("databases")
    return DemoShopBuilder().build(folder / "demo_shop.sqlite")


def dump(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as connection:
        return list(connection.iterdump())


def rows(path: Path, sql: str) -> list[tuple]:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(sql).fetchall()


def value(path: Path, sql: str) -> object:
    [(result,)] = rows(path, sql)
    return result


def count(path: Path, table: str) -> int:
    [(result,)] = rows(path, f"SELECT count(*) FROM {table}")
    return result


def test_build_writes_the_file_and_the_sidecar(built: Path):
    assert built.name == "demo_shop.sqlite"
    assert json.loads(built.with_suffix(".json").read_text(encoding="utf-8")) == SIDECAR
    assert sorted(path.name for path in built.parent.iterdir()) == [
        "demo_shop.json",
        "demo_shop.sqlite",
    ]


def test_two_builds_have_the_same_contents(built: Path, tmp_path: Path):
    again = DemoShopBuilder().build(tmp_path / "demo_shop.sqlite")
    assert dump(again) == dump(built)


def test_another_seed_gives_other_contents(built: Path, tmp_path: Path):
    other = DemoShopBuilder(seed=SEED + 1).build(tmp_path / "demo_shop.sqlite")
    assert dump(other) != dump(built)


def test_build_replaces_an_existing_file(built: Path, tmp_path: Path):
    path = tmp_path / "demo_shop.sqlite"
    path.write_bytes(b"old")
    DemoShopBuilder().build(path)
    assert dump(path) == dump(built)


def test_every_table_is_there(built: Path):
    names = rows(
        built, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
    assert [name for (name,) in names] == TABLES


def test_foreign_keys_are_declared_and_hold(built: Path):
    assert rows(built, "PRAGMA foreign_key_check") == []
    declared = {
        table: sorted(
            {row[2] for row in rows(built, f"PRAGMA foreign_key_list({table})")}
        )
        for table in TABLES
    }
    assert declared == {
        "categories": [],
        "customers": [],
        "order_items": ["orders", "products"],
        "orders": ["customers", "staff", "stores"],
        "products": ["categories"],
        "returns": ["order_items"],
        "staff": ["staff", "stores"],
        "stores": [],
    }


def test_row_counts(built: Path):
    counts = {table: count(built, table) for table in TABLES}
    assert counts["categories"] == 8
    assert counts["stores"] == 6
    assert counts["customers"] == CUSTOMERS
    assert counts["orders"] == ORDERS
    assert 50 <= counts["products"] <= 80
    assert 30 <= counts["staff"] <= 60
    assert 1.5 * ORDERS <= counts["order_items"] <= 3 * ORDERS
    assert (
        0.01 * counts["order_items"]
        <= counts["returns"]
        <= 0.08 * counts["order_items"]
    )
    assert 2_000 <= sum(counts.values()) <= 15_000


def test_orders_spread_over_two_years(built: Path):
    first, last = rows(built, "SELECT min(ordered_at), max(ordered_at) FROM orders")[0]
    assert first[:7] == "2024-01"
    assert last[:7] == "2025-12"
    assert FIRST_DAY.isoformat() <= first[:10]
    assert last[:10] <= LAST_DAY.isoformat()
    months = value(
        built, "SELECT count(DISTINCT strftime('%Y-%m', ordered_at)) FROM orders"
    )
    assert months == 24
    per_year = rows(
        built,
        "SELECT strftime('%Y', ordered_at), count(*) FROM orders GROUP BY 1 ORDER BY 1",
    )
    assert [year for year, _ in per_year] == ["2024", "2025"]
    assert all(count > ORDERS / 3 for _, count in per_year)


def test_prices_and_quantities_are_realistic(built: Path):
    low, high = rows(built, "SELECT min(unit_price), max(unit_price) FROM products")[0]
    assert 2 <= low < 5
    assert 100 < high <= 400
    assert value(built, "SELECT count(*) FROM products WHERE unit_cost <= 0") == 0
    assert (
        value(built, "SELECT count(*) FROM products WHERE unit_cost >= unit_price") == 0
    )
    assert rows(built, "SELECT min(quantity), max(quantity) FROM order_items")[0] == (
        1,
        5,
    )
    discounts = rows(built, "SELECT DISTINCT discount FROM order_items ORDER BY 1")
    assert [discount for (discount,) in discounts] == [0, 0.05, 0.1, 0.15, 0.2]
    assert value(built, "SELECT count(*) FROM order_items WHERE unit_price <= 0") == 0


def test_orders_follow_the_calendar(built: Path):
    checks = [
        "SELECT count(*) FROM orders AS o JOIN customers AS c USING (customer_id) "
        "WHERE date(o.ordered_at) < c.joined_on",
        "SELECT count(*) FROM orders AS o JOIN stores AS s USING (store_id) "
        "WHERE date(o.ordered_at) < s.opened_on",
        "SELECT count(*) FROM orders AS o JOIN staff AS s USING (staff_id) "
        "WHERE date(o.ordered_at) < s.hired_on OR s.store_id <> o.store_id",
        "SELECT count(*) FROM orders WHERE channel = 'online' "
        "AND (store_id IS NOT NULL OR staff_id IS NOT NULL)",
        "SELECT count(*) FROM orders WHERE channel = 'store' "
        "AND (store_id IS NULL OR staff_id IS NULL)",
        "SELECT count(*) FROM returns AS r JOIN order_items AS i USING (order_item_id) "
        "JOIN orders AS o USING (order_id) WHERE r.returned_on <= date(o.ordered_at) "
        "OR r.quantity > i.quantity OR o.status <> 'completed' "
        "OR r.refund > i.quantity * i.unit_price * (1 - i.discount) + 0.006",
    ]
    assert [value(built, sql) for sql in checks] == [0] * len(checks)


def test_return_reasons_fit_the_channel(built: Path):
    reasons = rows(
        built,
        "SELECT o.channel, group_concat(DISTINCT r.reason) FROM returns AS r "
        "JOIN order_items AS i USING (order_item_id) JOIN orders AS o USING (order_id) "
        "GROUP BY o.channel ORDER BY o.channel",
    )
    by_channel = {channel: set(found.split(",")) for channel, found in reasons}
    assert by_channel == {
        "online": {
            "Changed mind",
            "Damaged in transit",
            "Defective",
            "Not as described",
            "Wrong item sent",
        },
        "store": {"Changed mind", "Defective", "Not as described"},
    }


def test_returns_end_with_the_data(built: Path):
    first, last = rows(built, "SELECT min(returned_on), max(returned_on) FROM returns")[
        0
    ]
    assert FIRST_DAY.isoformat() < first
    assert last <= LAST_DAY.isoformat()


def test_both_channels_and_statuses_occur(built: Path):
    assert rows(built, "SELECT DISTINCT channel FROM orders ORDER BY 1") == [
        ("online",),
        ("store",),
    ]
    assert rows(built, "SELECT DISTINCT status FROM orders ORDER BY 1") == [
        ("cancelled",),
        ("completed",),
    ]


def test_the_registry_lists_the_demo_and_queries_run(built: Path):
    registry = DatabaseRegistry(built.parent)
    [info] = registry.databases()
    assert (info.id, info.title, info.description) == (
        "demo_shop",
        SIDECAR["title"],
        SIDECAR["description"],
    )
    assert info.table_count == 8
    database = registry.get("demo_shop")
    schema = SchemaInspector(database).inspect()
    assert schema.table_names() == TABLES
    sql = SqlValidator(schema).validate(REVENUE_BY_MONTH)
    result = QueryExecutor(database, max_rows=100, timeout_ms=5000).execute(sql)
    assert len(result.rows) == 24
    assert all(isinstance(revenue, float) and revenue > 0 for _, revenue in result.rows)


def test_destination_is_the_databases_folder():
    assert Path(__file__).parents[2] / "data" / "databases" / "demo_shop.sqlite" == (
        DESTINATION
    )


def test_committed_sidecar_matches_the_script():
    sidecar = DESTINATION.with_suffix(".json").read_text(encoding="utf-8")
    assert json.loads(sidecar) == SIDECAR
