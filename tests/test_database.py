import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from text_to_sql_demo.database import (
    Database,
    quote_identifier,
    table_names,
    to_sql_value,
)


def test_database_keeps_its_id_and_path(database: Database, database_path: Path):
    assert database.id == "fixture"
    assert database.path == database_path.resolve()


def test_writes_are_rejected(database: Database):
    with (
        database.connect() as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        connection.execute("INSERT INTO Teacher VALUES (4, 'Dana Evans')")


def test_query_only_is_on(database: Database):
    with database.connect() as connection:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)


def test_query_only_blocks_temp_tables(database: Database):
    with (
        database.connect() as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        connection.execute("CREATE TEMP TABLE scratch (a INTEGER)")


def test_defensive_mode_is_on(database: Database):
    with database.connect() as connection:
        assert connection.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE) is True


def test_connects_without_defensive_mode_when_sqlite_lacks_it(
    database: Database, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE")
    with database.connect() as connection:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)


def test_each_connect_opens_and_closes_a_connection(database: Database):
    with database.connect() as first, database.connect() as second:
        assert first is not second
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")


def test_path_with_uri_characters_opens(
    tmp_path: Path, build_database: Callable[[Path], Path]
):
    folder = tmp_path / (
        "odd #name 100%" if sys.platform == "win32" else "odd #name? 100%"
    )
    folder.mkdir()
    database = Database("odd", build_database(folder / "fixture.sqlite"))
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM Teacher").fetchone() == (3,)


def test_table_names_skip_internal_tables(tmp_path: Path):
    path = tmp_path / "log.sqlite"
    with closing(sqlite3.connect(path)) as setup:
        setup.execute("CREATE TABLE Log (Id INTEGER PRIMARY KEY AUTOINCREMENT, Line)")
        setup.execute("CREATE TABLE Audit (Id INTEGER PRIMARY KEY)")
        setup.execute("CREATE VIEW Lines AS SELECT Line FROM Log")
        setup.execute("INSERT INTO Log (Line) VALUES ('x')")
        setup.commit()
    with Database("log", path).connect() as connection:
        assert table_names(connection) == ["Audit", "Log"]


def test_table_names_skip_shadow_tables_of_virtual_tables(tmp_path: Path):
    path = tmp_path / "search.sqlite"
    with closing(sqlite3.connect(path)) as setup:
        setup.execute("CREATE VIRTUAL TABLE docs USING fts5(body)")
        setup.execute("CREATE VIRTUAL TABLE boxes USING rtree(id, x0, x1)")
        setup.execute("CREATE TABLE notes (a)")
        setup.commit()
    with Database("search", path).connect() as connection:
        assert table_names(connection) == ["boxes", "docs", "notes"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1), (1.5, 1.5), ("x", "x"), (None, None), (b"\x00\x01", "<2 bytes>")],
)
def test_to_sql_value(value: object, expected: object):
    assert to_sql_value(value) == expected


def test_quote_identifier_escapes_double_quotes():
    assert quote_identifier("Teacher") == '"Teacher"'
    assert quote_identifier('a"b') == '"a""b"'
