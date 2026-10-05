import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from text_to_sql_demo.database import Database
from text_to_sql_demo.errors import DatabaseUnreadableError, UnknownTableError
from text_to_sql_demo.schema import DatabaseSchema, Reference, SchemaInspector


def test_tables_are_sorted_with_row_counts(schema: DatabaseSchema):
    assert schema.table_names() == ["Course", "Teacher", "Video"]
    assert [table.row_count for table in schema.tables] == [3, 3, 30]


def test_columns_carry_types_and_keys(schema: DatabaseSchema):
    course = schema.find("Course")
    assert course is not None
    columns = {column.name: column for column in course.columns}
    assert list(columns) == ["CourseId", "Title", "TeacherId"]
    assert columns["CourseId"].primary_key is True
    assert columns["CourseId"].type == "INTEGER"
    assert columns["Title"].primary_key is False
    assert columns["Title"].type == "NVARCHAR(160)"
    assert columns["Title"].references is None
    assert columns["TeacherId"].references == Reference(
        table="Teacher", column="TeacherId"
    )


def test_find_ignores_case(schema: DatabaseSchema):
    table = schema.find("TEACHER")
    assert table is not None
    assert table.name == "Teacher"
    assert schema.find("Nope") is None


def test_schema_names_its_database(schema: DatabaseSchema):
    assert schema.database == "fixture"


def test_dump_has_exactly_the_contract_keys(schema: DatabaseSchema):
    dumped = schema.model_dump()
    assert set(dumped) == {"database", "tables"}
    table = dumped["tables"][0]
    assert set(table) == {"name", "row_count", "columns"}
    assert set(table["columns"][0]) == {"name", "type", "primary_key", "references"}
    reference = dumped["tables"][0]["columns"][2]["references"]
    assert reference == {"table": "Teacher", "column": "TeacherId"}


def test_describe_adds_sample_values(database: Database):
    description = SchemaInspector(database).describe("video")
    assert description.name == "Video"
    assert description.row_count == 30
    columns = {column.name: column for column in description.columns}
    assert columns["Name"].samples == ["Video 1", "Video 2", "Video 3"]
    assert columns["UnitPrice"].samples == [0.99]
    assert columns["CourseId"].references == Reference(
        table="Course", column="CourseId"
    )


def test_describe_unknown_table_lists_tables(database: Database):
    with pytest.raises(UnknownTableError, match="Course, Teacher, Video"):
        SchemaInspector(database).describe("Nope")


def test_samples_skip_nulls_and_blobs(tmp_path: Path):
    path = tmp_path / "blobs.sqlite"
    with sqlite3.connect(path) as setup:
        setup.execute(
            "CREATE TABLE Item (Id INTEGER PRIMARY KEY, Note TEXT, Data BLOB)"
        )
        setup.execute("INSERT INTO Item VALUES (1, NULL, x'0001'), (2, 'b', NULL)")
    setup.close()
    columns = {
        c.name: c
        for c in SchemaInspector(Database("blobs", path)).describe("Item").columns
    }
    assert columns["Note"].samples == ["b"]
    assert columns["Data"].samples == ["<2 bytes>"]


def test_reference_without_column_resolves_to_primary_key(tmp_path: Path):
    path = tmp_path / "implicit.sqlite"
    with sqlite3.connect(path) as setup:
        setup.executescript(
            """
            CREATE TABLE Teacher (TeacherId INTEGER PRIMARY KEY, Name TEXT);
            CREATE TABLE Course (CourseId INTEGER PRIMARY KEY, TeacherId INTEGER REFERENCES teacher);
            CREATE TABLE Log (Id INTEGER PRIMARY KEY AUTOINCREMENT, Line TEXT);
            INSERT INTO Log (Line) VALUES ('x');
            """
        )
    setup.close()
    schema = SchemaInspector(Database("implicit", path)).inspect()
    course = schema.find("Course")
    assert course is not None
    assert course.columns[1].references == Reference(
        table="Teacher", column="TeacherId"
    )
    assert schema.table_names() == ["Course", "Log", "Teacher"]


def test_a_damaged_page_is_reported_as_unreadable(
    tmp_path: Path, build_damaged_database: Callable[[Path], Path]
):
    path = build_damaged_database(tmp_path / "damaged.sqlite")
    inspector = SchemaInspector(Database("damaged", path))
    with pytest.raises(DatabaseUnreadableError, match="'damaged' could not be read"):
        inspector.inspect()
    with pytest.raises(DatabaseUnreadableError, match="malformed"):
        inspector.describe("big")
