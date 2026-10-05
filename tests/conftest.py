import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from text_to_sql_demo.database import Database
from text_to_sql_demo.registry import DatabaseRegistry
from text_to_sql_demo.schema import DatabaseSchema, SchemaInspector
from text_to_sql_demo.settings import Settings

FIXTURE_SQL = """
CREATE TABLE Teacher (
    TeacherId INTEGER PRIMARY KEY,
    Name NVARCHAR(120)
);
CREATE TABLE Course (
    CourseId INTEGER PRIMARY KEY,
    Title NVARCHAR(160) NOT NULL,
    TeacherId INTEGER NOT NULL REFERENCES Teacher (TeacherId)
);
CREATE TABLE Video (
    VideoId INTEGER PRIMARY KEY,
    Name NVARCHAR(200) NOT NULL,
    CourseId INTEGER REFERENCES Course (CourseId),
    Seconds INTEGER NOT NULL,
    UnitPrice NUMERIC(10, 2) NOT NULL
);
INSERT INTO Teacher VALUES (1, 'Ada Byrne'), (2, 'Ben Clarke'), (3, 'Cara Diaz');
INSERT INTO Course VALUES
    (1, 'Intro to SQL', 1),
    (2, 'Joins in practice', 1),
    (3, 'Window functions', 2);
"""


def build_fixture_database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(FIXTURE_SQL)
        connection.executemany(
            "INSERT INTO Video VALUES (?, ?, ?, ?, ?)",
            [(n, f"Video {n}", n % 3 + 1, 180 + n, 0.99) for n in range(1, 31)],
        )
        connection.commit()
    finally:
        connection.close()
    return path


def write_damaged_database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE big (a TEXT)")
        connection.executemany("INSERT INTO big VALUES (?)", [("x" * 200,)] * 200)
        connection.commit()
        (page_size,) = connection.execute("PRAGMA page_size").fetchone()
    finally:
        connection.close()
    data = bytearray(path.read_bytes())
    data[page_size : 2 * page_size] = b"\xff" * page_size
    path.write_bytes(bytes(data))
    return path


@pytest.fixture
def build_database() -> Callable[[Path], Path]:
    return build_fixture_database


@pytest.fixture
def build_damaged_database() -> Callable[[Path], Path]:
    return write_damaged_database


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def databases_dir(data_dir: Path) -> Path:
    folder = data_dir / "databases"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def database_path(databases_dir: Path) -> Path:
    return build_fixture_database(databases_dir / "fixture.sqlite")


@pytest.fixture
def registry(databases_dir: Path) -> DatabaseRegistry:
    return DatabaseRegistry(databases_dir)


@pytest.fixture
def database(registry: DatabaseRegistry, database_path: Path) -> Database:
    return registry.get("fixture")


@pytest.fixture
def connection(database: Database) -> Iterator[sqlite3.Connection]:
    with database.connect() as connection:
        yield connection


@pytest.fixture
def schema(database: Database) -> DatabaseSchema:
    return SchemaInspector(database).inspect()


@pytest.fixture
def settings(database_path: Path, data_dir: Path) -> Settings:
    return Settings.model_validate(
        {
            "TEXT_TO_SQL_DEMO_DATA_DIR": str(data_dir),
            "TEXT_TO_SQL_DEMO_MODEL": "gpt-6-luna",
            "OPENAI_API_KEY": "sk-test",
            "TEXT_TO_SQL_DEMO_MAX_ROWS": 10,
            "TEXT_TO_SQL_DEMO_QUERY_TIMEOUT_MS": 1000,
        }
    )
