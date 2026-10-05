import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from text_to_sql_demo.database import Database
from text_to_sql_demo.errors import (
    ForbiddenOperationError,
    QueryFailedError,
    QueryTimeoutError,
    UnknownTableError,
)
from text_to_sql_demo.execution import QueryExecutor, SqlResult
from text_to_sql_demo.safety import SqlValidator
from text_to_sql_demo.schema import DatabaseSchema


def sqlite_has_dbstat() -> bool:
    with closing(sqlite3.connect(":memory:")) as connection:
        options = {row[0] for row in connection.execute("PRAGMA compile_options")}
    return "ENABLE_DBSTAT_VTAB" in options


ENDLESS = (
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) "
    "SELECT count(*) FROM n"
)
HEAVY_ENDLESS = (
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) "
    "SELECT sum(length(replace(printf('%.*c', 900000, 'x'), 'x', 'y'))) FROM n"
)
DECOY_JOIN_ON = (
    "SELECT (SELECT 1 FROM (WITH sqlite_master AS (SELECT 1 AS sql) "
    "SELECT sql FROM sqlite_master)) FROM Teacher JOIN Course "
    "ON (SELECT sql LIKE 'CREATE TABLE%' FROM sqlite_master ORDER BY name LIMIT 1) LIMIT 1"
)

COMMON_FUNCTIONS = [
    "SELECT count(*), count(DISTINCT CourseId), sum(Seconds), avg(UnitPrice), "
    "min(Name), max(Name), total(Seconds), group_concat(Name, ', ') FROM Video",
    "SELECT upper(Name), lower(Name), length(Name), substr(Name, 1, 3), "
    "substring(Name, 2), replace(Name, 'A', 'a'), trim(Name), ltrim(Name), rtrim(Name), "
    "instr(Name, 'C'), coalesce(Name, ''), ifnull(Name, ''), nullif(Name, ''), "
    "iif(TeacherId > 1, 'y', 'n'), Name || '!' FROM Teacher",
    "SELECT round(UnitPrice, 1), abs(-1), printf('%.2f', UnitPrice), "
    "format('%d', 1), CAST(UnitPrice AS TEXT), typeof(UnitPrice), hex('a'), "
    "quote('a'), char(65), unicode('A'), sign(-2), concat(Name, '!'), "
    "concat_ws('-', Name, 'x') FROM Video",
    "SELECT strftime('%Y', 'now'), date('now'), datetime('now', '-1 day'), "
    "julianday('now'), time('now'), unixepoch('now'), CURRENT_DATE, "
    "CURRENT_TIME, CURRENT_TIMESTAMP",
    "SELECT row_number() OVER (ORDER BY Name), rank() OVER (ORDER BY CourseId), "
    "dense_rank() OVER (ORDER BY CourseId), percent_rank() OVER (ORDER BY CourseId), "
    "cume_dist() OVER (ORDER BY CourseId), ntile(4) OVER (ORDER BY VideoId), "
    "lag(Name) OVER (ORDER BY VideoId), lead(Name, 2) OVER (ORDER BY VideoId), "
    "first_value(Name) OVER (ORDER BY VideoId), last_value(Name) OVER (ORDER BY VideoId), "
    "nth_value(Name, 2) OVER (ORDER BY VideoId), sum(Seconds) OVER (PARTITION BY CourseId) "
    "FROM Video",
    "SELECT Name FROM Teacher WHERE Name LIKE 'A%' OR Name GLOB 'B*' OR Name NOT LIKE '%x%' ESCAPE '!'",
    "SELECT sqrt(4), power(2, 3), pow(2, 2), floor(1.5), ceil(1.5), ceiling(1.5), "
    "ln(1), log(100), log10(100), log2(8), exp(1), mod(7, 3), 7 % 3, pi(), "
    "trunc(1.5), degrees(1), radians(1), sin(0), cos(0), tan(0)",
    "SELECT json_extract('{\"a\": 1}', '$.a'), json_array(1, 2), json_object('a', 1), "
    "json_type('[]'), json_valid('{}'), json_array_length('[1]'), "
    "'{\"a\": 1}' -> '$.a', '{\"a\": 1}' ->> '$.a', json('[1]')",
    "SELECT json_group_array(Name), json_group_object(Name, TeacherId) FROM Teacher",
    "SELECT Name FROM Teacher ORDER BY random() LIMIT 1",
    "SELECT CASE WHEN CourseId BETWEEN 1 AND 2 THEN 'low' ELSE 'high' END, "
    "CourseId IN (1, 2), Name IS NOT NULL FROM Video",
]


@pytest.fixture
def executor(database: Database) -> QueryExecutor:
    return QueryExecutor(database, max_rows=10, timeout_ms=1000)


def run(database: Database, sql: str, *, max_rows: int = 10) -> SqlResult:
    return QueryExecutor(database, max_rows=max_rows, timeout_ms=1000).execute(sql)


def test_more_rows_than_the_limit_are_truncated(database: Database):
    sql = "SELECT VideoId FROM Video ORDER BY VideoId"
    result = run(database, sql)
    assert result.sql == sql
    assert result.columns == ["VideoId"]
    assert result.rows[0] == [1]
    assert len(result.rows) == 10
    assert result.row_count == 10
    assert result.truncated is True


def test_exactly_the_limit_is_not_truncated(database: Database):
    result = run(database, "SELECT VideoId FROM Video", max_rows=30)
    assert result.row_count == 30
    assert result.truncated is False


def test_elapsed_time_is_whole_milliseconds(database: Database):
    result = run(database, "SELECT 1")
    assert isinstance(result.elapsed_ms, int)
    assert result.elapsed_ms >= 0


def test_long_query_is_stopped(database: Database):
    executor = QueryExecutor(database, max_rows=10, timeout_ms=50)
    started = time.monotonic()
    with pytest.raises(QueryTimeoutError, match="50 ms"):
        executor.execute(ENDLESS)
    assert time.monotonic() - started < 5
    assert executor.execute("SELECT 1").rows == [[1]]


def test_heavy_functions_do_not_escape_the_time_limit(database: Database):
    executor = QueryExecutor(database, max_rows=10, timeout_ms=50)
    started = time.monotonic()
    with pytest.raises(QueryTimeoutError):
        executor.execute(HEAVY_ENDLESS)
    assert time.monotonic() - started < 1


def test_values_longer_than_the_limit_are_an_error(executor: QueryExecutor):
    with pytest.raises(QueryFailedError, match="longer than 1000000 bytes"):
        executor.execute(
            "SELECT length(replace(printf('%.*c', 900000, 'x'), 'x', 'yy'))"
        )


def test_oversized_printf_is_null_and_fast(database: Database):
    executor = QueryExecutor(database, max_rows=10, timeout_ms=50)
    started = time.monotonic()
    result = executor.execute(
        "SELECT length(replace(replace(replace(printf('%.*c', 50000000, 'x'), "
        "'x', 'yy'), 'y', 'zz'), 'z', 'q'))"
    )
    assert result.rows == [[None]]
    assert time.monotonic() - started < 0.5


def test_result_stops_at_the_byte_limit(executor: QueryExecutor):
    result = executor.execute("SELECT printf('%.*c', 900000, 'x') AS big FROM Video")
    assert result.row_count == 2
    assert result.truncated is True
    assert len(result.model_dump_json()) < executor.max_result_bytes + 1000


def test_first_row_over_the_byte_limit_is_an_error(executor: QueryExecutor):
    with pytest.raises(QueryFailedError, match="larger than 2000000 bytes"):
        executor.execute(
            "SELECT printf('%.*c', 900000, 'x'), printf('%.*c', 900000, 'y'), "
            "printf('%.*c', 900000, 'z')"
        )


@pytest.mark.parametrize(
    ("sql", "name"),
    [
        (DECOY_JOIN_ON, "sqlite_master"),
        ("SELECT * FROM sqlite_master", "sqlite_master"),
        ("SELECT * FROM sqlite_schema", "sqlite_master"),
        pytest.param(
            "SELECT * FROM dbstat",
            "dbstat",
            marks=pytest.mark.skipif(
                not sqlite_has_dbstat(), reason="this SQLite has no dbstat table"
            ),
        ),
        ("SELECT name FROM sqlite_temp_master", "sqlite_temp_master"),
        ("SELECT file FROM pragma_database_list", "pragma_database_list"),
        ("SELECT * FROM pragma_compile_options", "pragma_compile_options"),
        ("SELECT name FROM pragma_function_list", "pragma_function_list"),
        ("SELECT * FROM pragma_table_info('Teacher')", "pragma_table_info"),
    ],
)
def test_only_schema_tables_can_be_read(executor: QueryExecutor, sql: str, name: str):
    with pytest.raises(
        UnknownTableError, match=f"reads {name}.*Tables: Course, Teacher, Video"
    ):
        executor.execute(sql)


def test_pragma_without_columns_is_refused(executor: QueryExecutor):
    with pytest.raises(ForbiddenOperationError, match="PRAGMA function_list"):
        executor.execute("SELECT count(*) FROM pragma_function_list")


def test_counting_a_cte_is_allowed(executor: QueryExecutor):
    result = executor.execute(
        "WITH a AS MATERIALIZED (SELECT Name FROM Teacher) SELECT count(*) FROM a"
    )
    assert result.rows == [[3]]


@pytest.mark.parametrize(
    ("sql", "name"),
    [
        ("SELECT hex(fts3_tokenizer('simple'))", "fts3_tokenizer"),
        ("SELECT fts3_tokenizer('evil', x'4141414141414141')", "fts3_tokenizer"),
        ("SELECT load_extension('/tmp/x')", "load_extension"),
        ("SELECT sqlite_compileoption_get(0)", "sqlite_compileoption_get"),
        ("SELECT sqlite_version()", "sqlite_version"),
        ("SELECT length(randomblob(200000000))", "randomblob"),
        ("SELECT zeroblob(10)", "zeroblob"),
    ],
)
def test_only_allowed_functions_can_be_called(
    executor: QueryExecutor, sql: str, name: str
):
    with pytest.raises(ForbiddenOperationError, match=f"function {name}"):
        executor.execute(sql)


@pytest.mark.parametrize("sql", COMMON_FUNCTIONS)
def test_common_functions_run_after_validation(
    executor: QueryExecutor, schema: DatabaseSchema, sql: str
):
    assert executor.execute(SqlValidator(schema).validate(sql)).columns


def test_only_the_tables_of_its_own_database_can_be_read(
    tmp_path: Path, executor: QueryExecutor
):
    path = tmp_path / "other.sqlite"
    with closing(sqlite3.connect(path)) as setup:
        setup.execute("CREATE TABLE Region (RegionId INTEGER PRIMARY KEY, Name TEXT)")
        setup.execute("INSERT INTO Region VALUES (1, 'North')")
        setup.commit()
    other = QueryExecutor(Database("other", path), max_rows=10, timeout_ms=1000)
    assert other.execute("SELECT Name FROM Region").rows == [["North"]]
    with pytest.raises(QueryFailedError, match="no such table: Teacher"):
        other.execute("SELECT Name FROM Teacher")
    with pytest.raises(
        UnknownTableError, match=r"reads sqlite_master.*Tables: Region\."
    ):
        other.execute("SELECT * FROM sqlite_master")
    assert executor.execute("SELECT count(*) FROM Teacher").rows == [[3]]


def test_a_denied_query_does_not_affect_the_next_one(executor: QueryExecutor):
    with pytest.raises(UnknownTableError):
        executor.execute("SELECT * FROM sqlite_master")
    assert executor.execute("SELECT count(*) FROM Video").rows == [[30]]


@pytest.mark.parametrize("sql", ["SELECT ?", "SELECT :a", "SELECT @a", "SELECT $a"])
def test_placeholders_are_a_typed_error(executor: QueryExecutor, sql: str):
    with pytest.raises(QueryFailedError, match="bindings"):
        executor.execute(sql)


def test_sqlite_errors_are_typed(database: Database):
    with pytest.raises(QueryFailedError, match="no such function"):
        run(database, "SELECT no_such_function(1)")


def test_extensions_cannot_be_loaded(database: Database):
    with pytest.raises(ForbiddenOperationError, match="load_extension"):
        run(database, "SELECT load_extension('evil')")


def test_blobs_become_text(database: Database):
    assert run(database, "SELECT x'0001' AS data").rows == [["<2 bytes>"]]


def test_duplicate_output_names_keep_both_columns(database: Database):
    result = run(database, "SELECT Name, Name FROM Teacher")
    assert len(result.columns) == 2
    assert result.rows[0] == ["Ada Byrne", "Ada Byrne"]


def test_result_dump_matches_the_sql_result_frame(database: Database):
    dumped = run(database, "SELECT 1").model_dump()
    assert set(dumped) == {
        "sql",
        "columns",
        "rows",
        "row_count",
        "truncated",
        "elapsed_ms",
    }
