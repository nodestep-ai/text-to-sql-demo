import bisect
import itertools
import json
import random
import sqlite3
import sys
import unicodedata
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

SEED = 42
DESTINATION = (
    Path(__file__).resolve().parents[1] / "data" / "databases" / "demo_shop.sqlite"
)
SIDECAR = {
    "title": "Demo shop",
    "description": (
        "A made-up coffee and kitchen shop with six stores and an online shop. "
        "Orders from 2024 and 2025, with order items and returns. Prices in euros."
    ),
    "examples": [
        "Which ten products sold the most units?",
        "What is the revenue per month?",
        "Which store has the most returns?",
        "Which category brings in the most revenue?",
        "Write a report on sales by store in 2025.",
    ],
}
FIRST_DAY = date(2024, 1, 1)
LAST_DAY = date(2025, 12, 31)
PRICE_CHANGE = date(2025, 3, 1)
CUSTOMERS = 600
ORDERS = 2400

SCHEMA = """
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL
);
CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories (category_id),
    name TEXT NOT NULL,
    sku TEXT NOT NULL UNIQUE,
    unit_price NUMERIC(10, 2) NOT NULL,
    unit_cost NUMERIC(10, 2) NOT NULL
);
CREATE TABLE stores (
    store_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    opened_on DATE NOT NULL
);
CREATE TABLE staff (
    staff_id INTEGER PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores (store_id),
    manager_id INTEGER REFERENCES staff (staff_id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    role TEXT NOT NULL,
    hired_on DATE NOT NULL
);
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    joined_on DATE NOT NULL
);
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
    store_id INTEGER REFERENCES stores (store_id),
    staff_id INTEGER REFERENCES staff (staff_id),
    channel TEXT NOT NULL CHECK (channel IN ('store', 'online')),
    status TEXT NOT NULL CHECK (status IN ('completed', 'cancelled')),
    ordered_at DATETIME NOT NULL
);
CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders (order_id),
    product_id INTEGER NOT NULL REFERENCES products (product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(10, 2) NOT NULL,
    discount NUMERIC(3, 2) NOT NULL DEFAULT 0
);
CREATE TABLE returns (
    return_id INTEGER PRIMARY KEY,
    order_item_id INTEGER NOT NULL REFERENCES order_items (order_item_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    reason TEXT NOT NULL,
    returned_on DATE NOT NULL,
    refund NUMERIC(10, 2) NOT NULL
);
"""

COFFEES = [
    ("Ethiopia Yirgacheffe", 12.90),
    ("Kenya Nyeri", 13.50),
    ("Colombia Huila", 10.90),
    ("Brazil Cerrado", 9.50),
    ("Guatemala Antigua", 11.50),
    ("Costa Rica Tarrazú", 12.50),
    ("Sumatra Mandheling", 11.90),
    ("House Espresso Blend", 9.90),
    ("Decaf Colombia", 10.50),
]


class Category(NamedTuple):
    name: str
    description: str
    sku: str
    weight: float
    max_quantity: int
    products: list[tuple[str, float]]


CATEGORIES = [
    Category(
        "Coffee",
        "Whole bean coffee in 250 g and 1 kg bags",
        "COF",
        30,
        4,
        [(f"{name} 250 g", price) for name, price in COFFEES]
        + [(f"{name} 1 kg", round(price * 3.4) - 0.10) for name, price in COFFEES],
    ),
    Category(
        "Tea",
        "Loose leaf tea in 100 g tins",
        "TEA",
        14,
        4,
        [
            ("Assam Breakfast 100 g", 6.50),
            ("Darjeeling First Flush 100 g", 12.90),
            ("Sencha 100 g", 8.90),
            ("Jasmine Green 100 g", 7.90),
            ("Earl Grey 100 g", 6.90),
            ("Rooibos 100 g", 5.90),
            ("Chamomile 100 g", 5.50),
            ("Masala Chai 100 g", 7.50),
        ],
    ),
    Category(
        "Brewing equipment",
        "Brewers, grinders, kettles and machines",
        "BRW",
        8,
        1,
        [
            ("Pour-over dripper", 24.00),
            ("Glass carafe 600 ml", 29.00),
            ("French press 1 l", 34.90),
            ("Moka pot 6 cups", 39.90),
            ("Hand grinder", 69.00),
            ("Electric burr grinder", 189.00),
            ("Gooseneck kettle", 79.00),
            ("Coffee scale", 45.00),
            ("Milk frother", 59.00),
            ("Espresso machine", 349.00),
        ],
    ),
    Category(
        "Filters and care",
        "Filters, tampers, jugs and cleaning products",
        "ACC",
        10,
        3,
        [
            ("Paper filters, 100 pieces", 5.90),
            ("Cloth filter", 9.50),
            ("Espresso tamper", 24.90),
            ("Milk jug 350 ml", 16.90),
            ("Cleaning tablets, 20 pieces", 11.90),
            ("Descaler 500 ml", 12.50),
            ("Knock box", 29.00),
        ],
    ),
    Category(
        "Mugs and cups",
        "Mugs, cups and glasses",
        "MUG",
        10,
        4,
        [
            ("Stoneware mug", 14.00),
            ("Espresso cups, set of 2", 19.00),
            ("Double-wall glass 250 ml", 12.50),
            ("Travel mug", 24.00),
            ("Latte bowl", 16.00),
            ("Cappuccino cup and saucer", 13.50),
        ],
    ),
    Category(
        "Snacks",
        "Biscuits and chocolate",
        "SNK",
        14,
        5,
        [
            ("Almond biscotti", 4.90),
            ("Dark chocolate 70%", 3.90),
            ("Oat cookies", 3.50),
            ("Salted caramel bar", 2.90),
            ("Ginger snaps", 3.20),
            ("Hazelnut wafers", 2.50),
        ],
    ),
    Category(
        "Syrups",
        "Flavour syrups in 250 ml bottles",
        "SYR",
        8,
        3,
        [
            ("Vanilla syrup 250 ml", 7.90),
            ("Caramel syrup 250 ml", 7.90),
            ("Hazelnut syrup 250 ml", 7.90),
            ("Gingerbread syrup 250 ml", 8.50),
        ],
    ),
    Category(
        "Gifts",
        "Gift sets and kits",
        "GFT",
        6,
        2,
        [
            ("Coffee tasting set, 4 coffees", 32.00),
            ("Tea sampler, 6 teas", 24.00),
            ("Gift box with mug and coffee", 39.00),
            ("Barista starter kit", 119.00),
        ],
    ),
]

STORES = [
    ("Bratislava Old Town", "Bratislava", "Slovakia", date(2019, 4, 1), 1.3),
    ("Bratislava Petržalka", "Bratislava", "Slovakia", date(2021, 9, 1), 0.9),
    ("Košice Main Street", "Košice", "Slovakia", date(2020, 3, 1), 1.0),
    ("Vienna Neubau", "Vienna", "Austria", date(2022, 5, 1), 1.4),
    ("Graz Lend", "Graz", "Austria", date(2023, 2, 1), 0.8),
    ("Ljubljana Center", "Ljubljana", "Slovenia", date(2024, 9, 1), 1.0),
]
OTHER_CITIES = [
    ("Žilina", "Slovakia"),
    ("Nitra", "Slovakia"),
    ("Trnava", "Slovakia"),
    ("Linz", "Austria"),
    ("Salzburg", "Austria"),
    ("Maribor", "Slovenia"),
]
FIRST_NAMES = [
    "Anna",
    "Peter",
    "Jana",
    "Martin",
    "Lucia",
    "Tomáš",
    "Zuzana",
    "Michal",
    "Katarína",
    "Jakub",
    "Eva",
    "Lukas",
    "Sophie",
    "Maximilian",
    "Lena",
    "Felix",
    "Laura",
    "Jonas",
    "Nina",
    "David",
    "Maja",
    "Luka",
    "Tina",
    "Nejc",
    "Sara",
    "Marko",
    "Emma",
    "Daniel",
    "Julia",
    "Adam",
    "Hannah",
    "Samuel",
    "Veronika",
    "Filip",
    "Tereza",
    "Oliver",
    "Klara",
    "Matej",
    "Ivana",
    "Paul",
]
LAST_NAMES = [
    "Novák",
    "Kováč",
    "Horváth",
    "Varga",
    "Tóth",
    "Baláž",
    "Szabó",
    "Molnár",
    "Krajčí",
    "Hudák",
    "Gruber",
    "Huber",
    "Bauer",
    "Wagner",
    "Pichler",
    "Steiner",
    "Moser",
    "Mayer",
    "Hofer",
    "Leitner",
    "Novak",
    "Horvat",
    "Kovačič",
    "Krajnc",
    "Zupan",
    "Potočnik",
    "Mlakar",
    "Kos",
    "Vidmar",
    "Golob",
    "Svoboda",
    "Dvořák",
    "Černý",
    "Procházka",
    "Kučera",
    "Smith",
    "Brown",
    "Rossi",
    "Müller",
    "Schmidt",
]
RETURN_REASONS = {
    "online": {
        "Damaged in transit": 3,
        "Defective": 3,
        "Wrong item sent": 2,
        "Not as described": 2,
        "Changed mind": 4,
    },
    "store": {"Defective": 3, "Not as described": 2, "Changed mind": 4},
}
DISCOUNTS = [0.05, 0.10, 0.15, 0.20]


class Product(NamedTuple):
    product_id: int
    category: Category
    price: float


class Store(NamedTuple):
    store_id: int
    opened_on: date
    weight: float


class Employee(NamedTuple):
    staff_id: int
    store_id: int
    hired_on: date


class Tables(NamedTuple):
    categories: list[tuple]
    products: list[tuple]
    stores: list[tuple]
    staff: list[tuple]
    customers: list[tuple]
    orders: list[tuple]
    order_items: list[tuple]
    returns: list[tuple]


class DemoShopBuilder:
    def __init__(self, seed: int = SEED) -> None:
        self.seed = seed

    def build(self, destination: Path) -> Path:
        tables = self._generate(random.Random(self.seed))
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.part")
        partial.unlink(missing_ok=True)
        try:
            self._write(partial, tables)
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
        destination.with_suffix(".json").write_text(
            json.dumps(SIDECAR, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return destination

    @staticmethod
    def _write(path: Path, tables: Tables) -> None:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA)
            for name, rows in tables._asdict().items():
                marks = ", ".join("?" * len(rows[0]))
                connection.executemany(f"INSERT INTO {name} VALUES ({marks})", rows)
            connection.commit()

    def _generate(self, rng: random.Random) -> Tables:
        categories, products = self._catalog()
        stores, staff, employees = self._stores(rng)
        customers, customer_weights = self._customers(rng)
        orders, order_items, returns = self._orders(
            rng, products, employees, customers, customer_weights
        )
        return Tables(
            categories=categories,
            products=[
                (
                    product.product_id,
                    CATEGORIES.index(product.category) + 1,
                    name,
                    f"{product.category.sku}-{product.product_id:03d}",
                    product.price,
                    round(product.price * rng.uniform(0.38, 0.62), 2),
                )
                for product, name in products
            ],
            stores=stores,
            staff=staff,
            customers=customers,
            orders=orders,
            order_items=order_items,
            returns=returns,
        )

    @staticmethod
    def _catalog() -> tuple[list[tuple], list[tuple[Product, str]]]:
        categories = [
            (number, category.name, category.description)
            for number, category in enumerate(CATEGORIES, start=1)
        ]
        ids = itertools.count(1)
        products = [
            (Product(next(ids), category, round(price, 2)), name)
            for category in CATEGORIES
            for name, price in category.products
        ]
        return categories, products

    def _stores(
        self, rng: random.Random
    ) -> tuple[list[tuple], list[tuple], dict[int, list[Employee]]]:
        stores = [
            (number, name, city, country, opened_on.isoformat())
            for number, (name, city, country, opened_on, _) in enumerate(
                STORES, start=1
            )
        ]
        ids = itertools.count(1)
        head = next(ids)
        staff = [(head, 1, None, *self._name(rng), "Head of operations", "2019-01-07")]
        employees: dict[int, list[Employee]] = {}
        for store_id, (_, _, _, opened_on, _) in enumerate(STORES, start=1):
            manager = next(ids)
            manager_hired = opened_on - timedelta(days=30)
            staff.append(
                (
                    manager,
                    store_id,
                    head,
                    *self._name(rng),
                    "Store manager",
                    manager_hired.isoformat(),
                )
            )
            team = [Employee(manager, store_id, manager_hired)]
            for position in range(rng.randint(4, 7)):
                staff_id = next(ids)
                hired_on = (
                    opened_on - timedelta(days=14)
                    if position < 2
                    else self._day(rng, opened_on, date(2025, 10, 1))
                )
                role = rng.choice(["Barista", "Barista", "Sales associate"])
                staff.append(
                    (
                        staff_id,
                        store_id,
                        manager,
                        *self._name(rng),
                        role,
                        hired_on.isoformat(),
                    )
                )
                team.append(Employee(staff_id, store_id, hired_on))
            employees[store_id] = team
        return stores, staff, employees

    def _customers(self, rng: random.Random) -> tuple[list[tuple], list[float]]:
        home_cities = [(city, country) for _, city, country, _, _ in STORES]
        joined = sorted(
            self._day(rng, date(2022, 6, 1), date(2025, 11, 30))
            for _ in range(CUSTOMERS)
        )
        customers = []
        for customer_id, joined_on in enumerate(joined, start=1):
            first, last = self._name(rng)
            city, country = rng.choice(
                home_cities if rng.random() < 0.75 else OTHER_CITIES
            )
            email = f"{self._ascii(first)}.{self._ascii(last)}{customer_id}@example.com"
            customers.append(
                (
                    customer_id,
                    first,
                    last,
                    email.lower(),
                    city,
                    country,
                    joined_on.isoformat(),
                )
            )
        weights = [rng.expovariate(1.0) + 0.05 for _ in customers]
        return customers, weights

    def _orders(
        self,
        rng: random.Random,
        products: list[tuple[Product, str]],
        employees: dict[int, list[Employee]],
        customers: list[tuple],
        customer_weights: list[float],
    ) -> tuple[list[tuple], list[tuple], list[tuple]]:
        catalog = [product for product, _ in products]
        by_category = {
            category.name: [p for p in catalog if p.category is category]
            for category in CATEGORIES
        }
        branches = [
            Store(number, opened_on, weight)
            for number, (_, _, _, opened_on, weight) in enumerate(STORES, start=1)
        ]
        joined = [date.fromisoformat(row[6]) for row in customers]
        cumulative = list(itertools.accumulate(customer_weights))
        days = [
            FIRST_DAY + timedelta(days=n)
            for n in range((LAST_DAY - FIRST_DAY).days + 1)
        ]
        dates = sorted(
            rng.choices(days, weights=[self._traffic(d) for d in days], k=ORDERS)
        )
        orders: list[tuple] = []
        items: list[tuple] = []
        returns: list[tuple] = []
        for order_id, day in enumerate(dates, start=1):
            eligible = bisect.bisect_right(joined, day)
            [customer] = rng.choices(
                customers[:eligible], cum_weights=cumulative[:eligible]
            )
            if rng.random() < 0.35:
                channel, store_id, staff_id = "online", None, None
                hour = rng.randint(0, 23)
            else:
                open_stores = [s for s in branches if s.opened_on <= day]
                [store] = rng.choices(
                    open_stores, weights=[s.weight for s in open_stores]
                )
                team = [
                    employee
                    for employee in employees[store.store_id]
                    if employee.hired_on <= day
                ]
                channel, store_id, staff_id = (
                    "store",
                    store.store_id,
                    rng.choice(team).staff_id,
                )
                hour = rng.randint(8, 18)
            cancelled = rng.random() < (0.05 if channel == "online" else 0.02)
            ordered_at = f"{day.isoformat()} {hour:02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
            orders.append(
                (
                    order_id,
                    customer[0],
                    store_id,
                    staff_id,
                    channel,
                    "cancelled" if cancelled else "completed",
                    ordered_at,
                )
            )
            for product in self._basket(rng, by_category, day):
                item_id = len(items) + 1
                quantity = self._quantity(rng, product.category.max_quantity)
                price = product.price
                if product.category.name == "Coffee" and day < PRICE_CHANGE:
                    price = round(price / 1.08, 1)
                discount = 0 if rng.random() < 0.85 else rng.choice(DISCOUNTS)
                items.append(
                    (item_id, order_id, product.product_id, quantity, price, discount)
                )
                chance = 0.07 if product.category.max_quantity == 1 else 0.02
                if not cancelled and rng.random() < chance:
                    returned = rng.randint(1, quantity)
                    reasons = RETURN_REASONS[channel]
                    [reason] = rng.choices(list(reasons), list(reasons.values()))
                    returned_on = day + timedelta(days=rng.randint(2, 30))
                    if returned_on > LAST_DAY:
                        continue
                    returns.append(
                        (
                            len(returns) + 1,
                            item_id,
                            returned,
                            reason,
                            returned_on.isoformat(),
                            round(returned * price * (1 - discount), 2),
                        )
                    )
        return orders, items, returns

    @staticmethod
    def _basket(
        rng: random.Random, by_category: dict[str, list[Product]], day: date
    ) -> list[Product]:
        lines = rng.choices([1, 2, 3, 4, 5], weights=[35, 30, 20, 10, 5])[0]
        names = [category.name for category in CATEGORIES]
        weights = [
            category.weight * (3 if category.name == "Gifts" and day.month >= 11 else 1)
            for category in CATEGORIES
        ]
        basket: list[Product] = []
        while len(basket) < lines:
            [name] = rng.choices(names, weights=weights)
            product = rng.choice(by_category[name])
            if product not in basket:
                basket.append(product)
        return basket

    @staticmethod
    def _quantity(rng: random.Random, most: int) -> int:
        choices = list(range(1, most + 1))
        return rng.choices(choices, weights=[1 / n**1.5 for n in choices])[0]

    @staticmethod
    def _traffic(day: date) -> float:
        month = {1: 0.8, 7: 0.85, 8: 0.85, 11: 1.3, 12: 1.7}.get(day.month, 1.0)
        weekend = 1.25 if day.weekday() >= 5 else 1.0
        growth = 1.15 if day.year == 2025 else 1.0
        return month * weekend * growth

    @staticmethod
    def _day(rng: random.Random, first: date, last: date) -> date:
        return first + timedelta(days=rng.randint(0, (last - first).days))

    @staticmethod
    def _name(rng: random.Random) -> tuple[str, str]:
        return rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)

    @staticmethod
    def _ascii(text: str) -> str:
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def main() -> int:
    path = DemoShopBuilder().build(DESTINATION)
    sys.stdout.write(f"Demo shop database at {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
