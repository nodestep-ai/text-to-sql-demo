import sqlite3
import time

import pytest
import sqlglot

from text_to_sql_demo.database import Database
from text_to_sql_demo.errors import (
    ForbiddenOperationError,
    MultipleStatementsError,
    QueryTooComplexError,
    SqlError,
    SqlSyntaxError,
    StatementNotAllowedError,
    UnknownColumnError,
    UnknownTableError,
    UnsupportedSyntaxError,
)
from text_to_sql_demo.execution import QueryExecutor
from text_to_sql_demo.safety import MAX_SQL_LENGTH, SqlValidator
from text_to_sql_demo.schema import DatabaseSchema

DECOY_JOIN_ON = (
    "SELECT (SELECT 1 FROM (WITH {name} AS (SELECT 1 AS {column}) "
    "SELECT {column} FROM {name})) FROM Teacher JOIN Course ON ({probe}) LIMIT 1"
)
MASTER_PROBE = DECOY_JOIN_ON.format(
    name="sqlite_master",
    column="sql",
    probe="SELECT sql LIKE 'CREATE TABLE%' FROM sqlite_master ORDER BY name LIMIT 1",
)
DATABASE_LIST_PROBE = DECOY_JOIN_ON.format(
    name="pragma_database_list",
    column="file",
    probe="SELECT file LIKE '/home/%' FROM pragma_database_list",
)
POSITIONAL_ORDER = (
    "SELECT a.Name, (SELECT count(*) FROM Course AS b WHERE b.TeacherId = a.TeacherId) "
    "FROM Teacher AS a ORDER BY 2 DESC"
)


def unspaced(text: str) -> str:
    return "".join(text.split())


def nested_parentheses(depth: int) -> str:
    return "SELECT " + "(" * depth + "1" + ")" * depth


def nested_subqueries(depth: int) -> str:
    source = "Teacher"
    for _ in range(depth):
        source = f"(SELECT * FROM {source})"
    return f"SELECT * FROM {source}"


def or_chain(terms: int) -> str:
    return "SELECT Name FROM Teacher WHERE " + " OR ".join(
        f"TeacherId = {number}" for number in range(terms)
    )


ALLOWED = [
    "SELECT Name FROM Teacher",
    "select name from teacher",
    'SELECT "Name" FROM "Teacher"',
    "SELECT a.Name, COUNT(*) AS courses FROM Teacher AS a JOIN Course AS al ON al.TeacherId = a.TeacherId GROUP BY a.Name ORDER BY courses DESC",
    "WITH long AS (SELECT * FROM Video WHERE Seconds > 190) SELECT COUNT(*) FROM long",
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 5) SELECT x FROM n",
    "SELECT Name FROM Teacher UNION SELECT Title FROM Course",
    "SELECT Name FROM Teacher EXCEPT SELECT Name FROM Teacher WHERE TeacherId = 1",
    "SELECT Name FROM Video WHERE CourseId IN (SELECT CourseId FROM Course WHERE TeacherId = 1)",
    "SELECT Name FROM Teacher AS a WHERE EXISTS (SELECT 1 FROM Course AS b WHERE b.TeacherId = a.TeacherId)",
    "SELECT x.n FROM (SELECT Name AS n FROM Teacher) AS x",
    "SELECT t.Name, t.UnitPrice * 2 AS price FROM Video AS t ORDER BY price",
    "SELECT Name, ROW_NUMBER() OVER (PARTITION BY CourseId ORDER BY Seconds) FROM Video",
    "SELECT * FROM Video",
    "SELECT 1",
    "SELECT strftime('%Y', 'now')",
    "SELECT Name FROM Teacher WHERE Name = 'Ada Byrne'",
    "SELECT Name FROM Teacher;",
    "SELECT Name FROM Teacher -- all teachers",
    POSITIONAL_ORDER,
    "SELECT Name, (SELECT 1) FROM Teacher ORDER BY 2",
    "SELECT (SELECT 1) ORDER BY 1",
    "SELECT rowid FROM Teacher",
    "SELECT a.rowid, a.Name FROM Teacher AS a",
    "SELECT ROWID, oid, _rowid_ FROM Teacher",
    "SELECT Name FROM Teacher GROUP BY Name HAVING count(*) > 0",
    "SELECT Name AS n, count(*) AS c FROM Teacher GROUP BY n HAVING c > 0 AND n <> ''",
    "WITH a AS (SELECT * FROM b), b AS (SELECT Name FROM Teacher) SELECT * FROM a",
    "SELECT * FROM (WITH x AS (SELECT Name FROM Teacher) SELECT * FROM x) AS y",
    "WITH x AS (SELECT Name FROM Teacher) SELECT * FROM x UNION SELECT * FROM x",
    or_chain(100),
]

MEANING = [
    "SELECT Seconds / 7, Seconds % 7, Seconds / 7.0, -Seconds FROM Video",
    "SELECT Name || ' (' || Seconds || ')' FROM Video",
    "SELECT round(sum(Seconds * UnitPrice * (1 - 0.1)), 2) FROM Video",
    "SELECT Seconds * (1 + 2) - (Seconds - 1) * 2 FROM Video",
    "SELECT CAST(UnitPrice * 100 AS INTEGER), CAST(Seconds AS TEXT) FROM Video",
    "SELECT Name FROM Video WHERE Seconds BETWEEN 190 AND 200 AND NOT CourseId = 2",
    "SELECT Name FROM Video WHERE CourseId = 1 OR CourseId = 2 AND Seconds > 200",
    "SELECT Name FROM Video WHERE (CourseId = 1 OR CourseId = 2) AND Seconds > 200",
    "SELECT Name FROM Video WHERE CourseId NOT IN (1, 3) AND Name LIKE 'video 1%'",
    "SELECT Name FROM Video WHERE Name GLOB 'Video 2*' AND CourseId IS NOT NULL",
    "SELECT CASE WHEN Seconds > 200 THEN 'long' WHEN Seconds > 190 THEN 'mid' ELSE 'short' END FROM Video",
    "SELECT iif(Seconds > 195, 1, 0), nullif(CourseId, 2), coalesce(NULL, Seconds) FROM Video",
    "SELECT DISTINCT CourseId FROM Video ORDER BY CourseId DESC LIMIT 2 OFFSET 1",
    "SELECT group_concat(Name, '; ') FROM Teacher",
    "SELECT substr(Name, 2, 3), upper(Name), length(Name) FROM Teacher",
    "SELECT Name, sum(Seconds) OVER (ORDER BY VideoId ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM Video",
    "SELECT CourseId, count(*) FILTER (WHERE Seconds > 195) FROM Video GROUP BY CourseId",
    "SELECT 1 = 1, 1 <> 2, 2 >= 1, 'a' < 'b', NULL IS NULL",
]

DENIED = [
    ("", SqlSyntaxError),
    (" ; ", SqlSyntaxError),
    ("SELECT (1", SqlSyntaxError),
    ("SELECT 'open", SqlSyntaxError),
    ("SELECT 1; SELECT 2", MultipleStatementsError),
    ("SELECT 1; DROP TABLE Teacher", MultipleStatementsError),
    ("INSERT INTO Teacher VALUES (4, 'Dana Evans')", StatementNotAllowedError),
    ("REPLACE INTO Teacher VALUES (1, 'x')", StatementNotAllowedError),
    ("UPDATE Teacher SET Name = 'x'", StatementNotAllowedError),
    ("DELETE FROM Teacher", StatementNotAllowedError),
    ("DROP TABLE Teacher", StatementNotAllowedError),
    ("CREATE TABLE t (a INTEGER)", StatementNotAllowedError),
    ("ALTER TABLE Teacher ADD COLUMN x INTEGER", StatementNotAllowedError),
    ("PRAGMA query_only = OFF", StatementNotAllowedError),
    ("ATTACH DATABASE 'other.db' AS other", StatementNotAllowedError),
    ("DETACH DATABASE other", StatementNotAllowedError),
    ("VACUUM", StatementNotAllowedError),
    ("BEGIN", StatementNotAllowedError),
    ("EXPLAIN SELECT 1", StatementNotAllowedError),
    ("VALUES (1)", StatementNotAllowedError),
    ("WITH x AS (SELECT 1) DELETE FROM Teacher", StatementNotAllowedError),
    (
        "WITH gone AS (DELETE FROM Teacher RETURNING *) SELECT * FROM gone",
        ForbiddenOperationError,
    ),
    ("SELECT * FROM Teachers", UnknownTableError),
    ("SELECT * FROM sqlite_master", UnknownTableError),
    ("SELECT * FROM pragma_table_info('Teacher')", UnknownTableError),
    ("SELECT * FROM json_each('[1]')", UnknownTableError),
    ("SELECT * FROM other.Teacher", UnknownTableError),
    ("SELECT * FROM main.Teacher", UnknownTableError),
    (
        "SELECT Name FROM Teacher WHERE TeacherId IN (SELECT TeacherId FROM Nope)",
        UnknownTableError,
    ),
    (
        "SELECT * FROM (WITH sqlite_master AS (SELECT 1) SELECT * FROM sqlite_master) AS a, sqlite_master",
        UnknownTableError,
    ),
    (MASTER_PROBE, UnknownTableError),
    (DATABASE_LIST_PROBE, UnknownTableError),
    ("SELECT * FROM (WITH x AS (SELECT 1) SELECT * FROM x) AS y, x", UnknownTableError),
    ("SELECT ?", UnsupportedSyntaxError),
    ("SELECT :a", UnsupportedSyntaxError),
    ("SELECT @a", UnsupportedSyntaxError),
    ("SELECT $a", UnsupportedSyntaxError),
    ("SELECT Name FROM Teacher WHERE TeacherId = ?", UnsupportedSyntaxError),
    ("SELECT 0x10, 1e3", UnsupportedSyntaxError),
    ("SELECT Name FROM Teacher WHERE TeacherId = 0x10", UnsupportedSyntaxError),
    ("SELECT x'10'", UnsupportedSyntaxError),
    (nested_parentheses(500), QueryTooComplexError),
    (nested_subqueries(200), QueryTooComplexError),
    (or_chain(2000), QueryTooComplexError),
    ("SELECT Nme FROM Teacher", UnknownColumnError),
    ("SELECT a.Title FROM Teacher AS a", UnknownColumnError),
    ("SELECT Name FROM Teacher ORDER BY Nme", UnknownColumnError),
    ("SELECT x.zz FROM (SELECT Name AS n FROM Teacher) AS x", UnknownColumnError),
    ("SELECT Name FROM Teacher GROUP BY Name HAVING Nope > 1", UnknownColumnError),
    (
        "SELECT Name FROM Teacher GROUP BY Name HAVING count(Nope) > 1",
        UnknownColumnError,
    ),
    ("SELECT rowid FROM (SELECT Name FROM Teacher)", UnknownColumnError),
]


def shortened_id(value: object) -> str | None:
    if isinstance(value, str) and len(value) > 80:
        return f"{value[:60]}... ({len(value)} characters)"
    return None


@pytest.fixture
def validator(schema: DatabaseSchema) -> SqlValidator:
    return SqlValidator(schema)


@pytest.mark.parametrize("sql", ALLOWED, ids=shortened_id)
def test_allowed(validator: SqlValidator, sql: str):
    assert validator.validate(sql)


@pytest.mark.parametrize(("sql", "error"), DENIED, ids=shortened_id)
def test_denied(validator: SqlValidator, sql: str, error: type[SqlError]):
    with pytest.raises(error):
        validator.validate(sql)


def test_validated_sql_is_pretty_printed_without_the_semicolon(
    validator: SqlValidator,
):
    assert (
        validator.validate("select Name from Teacher;")
        == "SELECT\n  Name\nFROM Teacher"
    )


def test_validated_sql_uppercases_keywords_and_functions(validator: SqlValidator):
    formatted = validator.validate(
        "select a.Name, count(*) as courses from Teacher a join Course c "
        "on c.TeacherId = a.TeacherId group by a.Name order by courses desc"
    )
    assert formatted == (
        "SELECT\n"
        "  a.Name,\n"
        "  COUNT(*) AS courses\n"
        "FROM Teacher AS a\n"
        "JOIN Course AS c\n"
        "  ON c.TeacherId = a.TeacherId\n"
        "GROUP BY\n"
        "  a.Name\n"
        "ORDER BY\n"
        "  courses DESC"
    )


@pytest.mark.parametrize("sql", ALLOWED, ids=shortened_id)
def test_formatted_sql_parses_to_the_checked_tree(validator: SqlValidator, sql: str):
    formatted = validator.validate(sql)
    assert sqlglot.parse_one(formatted, read="sqlite") == sqlglot.parse_one(
        sql, read="sqlite"
    )


@pytest.mark.parametrize("sql", ALLOWED, ids=shortened_id)
def test_formatted_sql_passes_the_validator_unchanged(
    validator: SqlValidator, sql: str
):
    formatted = validator.validate(sql)
    assert validator.validate(formatted) == formatted


@pytest.mark.parametrize("sql", ALLOWED + MEANING, ids=shortened_id)
def test_formatted_sql_returns_the_same_rows_as_the_input(
    validator: SqlValidator,
    database: Database,
    connection: sqlite3.Connection,
    sql: str,
):
    formatted = validator.validate(sql)
    assert "\n" in formatted
    expected = [list(row) for row in connection.execute(sql)]
    result = QueryExecutor(database, max_rows=1000, timeout_ms=1000).execute(formatted)
    assert result.rows == expected
    assert result.sql == formatted


@pytest.mark.parametrize("sql", ALLOWED + MEANING, ids=shortened_id)
def test_formatted_sql_names_columns_as_the_compact_rendering_up_to_spaces(
    validator: SqlValidator, connection: sqlite3.Connection, sql: str
):
    compact = sqlglot.parse_one(sql, read="sqlite").sql(dialect="sqlite")
    formatted = validator.validate(sql)
    expected = connection.execute(compact).description
    result = connection.execute(formatted).description
    assert [unspaced(column[0]) for column in result] == [
        unspaced(column[0]) for column in expected
    ]


@pytest.mark.parametrize(
    ("sql", "line"),
    [
        (
            "SELECT round(sum(Seconds * UnitPrice * (1 - 0.1)), 2) AS total FROM Video",
            "  ROUND(SUM(Seconds * UnitPrice * (1 - 0.1)), 2) AS total",
        ),
        ("SELECT (Seconds + 1) * 2 AS x FROM Video", "  (Seconds + 1) * 2 AS x"),
        (
            "SELECT Name FROM Video WHERE (CourseId = 1 OR CourseId = 2) AND Seconds > 200",
            "  (CourseId = 1 OR CourseId = 2) AND Seconds > 200",
        ),
    ],
)
def test_parentheses_around_an_expression_stay_on_one_line(
    validator: SqlValidator, sql: str, line: str
):
    assert line in validator.validate(sql).splitlines()


def test_an_unaliased_subquery_column_is_named_after_the_formatted_text(
    validator: SqlValidator, database: Database
):
    executor = QueryExecutor(database, max_rows=10, timeout_ms=1000)
    result = executor.execute(validator.validate("SELECT (SELECT 1), 2 AS two"))
    assert result.columns == ["(\n    SELECT\n      1\n  )", "two"]


def test_syntax_error_message_is_plain(validator: SqlValidator):
    with pytest.raises(SqlSyntaxError, match="line 1") as error:
        validator.validate("SELECT (1")
    assert "\x1b" not in str(error.value)


def test_empty_sql_says_so(validator: SqlValidator):
    with pytest.raises(SqlSyntaxError, match="No SQL statement"):
        validator.validate("  ")


def test_multiple_statements_says_one(validator: SqlValidator):
    with pytest.raises(MultipleStatementsError, match="one statement"):
        validator.validate("SELECT 1; SELECT 2")


def test_not_allowed_names_the_statement(validator: SqlValidator):
    with pytest.raises(StatementNotAllowedError, match="Only SELECT"):
        validator.validate("DELETE FROM Teacher")


@pytest.mark.parametrize(
    ("sql", "name"),
    [
        ("REINDEX", "REINDEX"),
        ("REINDEX Teacher", "REINDEX"),
        ("SAVEPOINT a", "SAVEPOINT"),
        ("RELEASE a", "RELEASE"),
    ],
)
def test_misparsed_statements_are_named_by_their_first_word(
    validator: SqlValidator, sql: str, name: str
):
    with pytest.raises(StatementNotAllowedError, match=f"Got {name}\\."):
        validator.validate(sql)


def test_forbidden_operation_names_the_operation(validator: SqlValidator):
    with pytest.raises(ForbiddenOperationError, match="DELETE"):
        validator.validate(
            "WITH gone AS (DELETE FROM Teacher RETURNING *) SELECT * FROM gone"
        )


def test_unknown_table_lists_tables(validator: SqlValidator):
    with pytest.raises(UnknownTableError, match="Tables: Course, Teacher, Video"):
        validator.validate("SELECT * FROM Teachers")


def test_table_function_is_named(validator: SqlValidator):
    with pytest.raises(UnknownTableError, match="Table-valued functions"):
        validator.validate("SELECT * FROM pragma_table_info('Teacher')")


def test_schema_prefix_is_explained(validator: SqlValidator):
    with pytest.raises(UnknownTableError, match="without a schema prefix"):
        validator.validate("SELECT * FROM main.Teacher")


def test_unknown_column_lists_columns(validator: SqlValidator):
    with pytest.raises(
        UnknownColumnError, match=r"Teacher\(TeacherId, Name\)"
    ) as error:
        validator.validate("SELECT Nme FROM Teacher")
    assert "single quotes" not in str(error.value)


def test_unknown_column_lists_every_table_of_the_query(validator: SqlValidator):
    with pytest.raises(
        UnknownColumnError,
        match=r"Teacher\(TeacherId, Name\), Course\(CourseId, Title, TeacherId\)",
    ):
        validator.validate(
            "SELECT a.Nme FROM Teacher AS a JOIN Course AS b ON a.TeacherId = b.TeacherId"
        )


def test_unknown_column_in_having_lists_columns(validator: SqlValidator):
    with pytest.raises(UnknownColumnError, match=r"nope.*Teacher\(TeacherId, Name\)"):
        validator.validate("SELECT Name FROM Teacher GROUP BY Name HAVING Nope > 1")


def test_placeholders_say_to_write_values_into_the_query(validator: SqlValidator):
    with pytest.raises(UnsupportedSyntaxError, match="Write values into the query"):
        validator.validate("SELECT Name FROM Teacher WHERE TeacherId = :id")


def test_hex_literals_say_to_use_decimal_numbers(validator: SqlValidator):
    with pytest.raises(UnsupportedSyntaxError, match="decimal"):
        validator.validate("SELECT Name FROM Teacher WHERE TeacherId = 0x10")


def test_long_sql_is_rejected_before_parsing(validator: SqlValidator):
    sql = or_chain(20000)
    started = time.monotonic()
    with pytest.raises(QueryTooComplexError, match=f"limit is {MAX_SQL_LENGTH}"):
        validator.validate(sql)
    assert time.monotonic() - started < 0.1


def test_deep_nesting_says_so(validator: SqlValidator):
    with pytest.raises(QueryTooComplexError, match="nested too deeply"):
        validator.validate(nested_parentheses(500))


def test_decoy_cte_names_the_real_table(validator: SqlValidator):
    with pytest.raises(UnknownTableError, match="'sqlite_master'"):
        validator.validate(MASTER_PROBE)


def test_double_quoted_text_gets_a_hint(validator: SqlValidator):
    with pytest.raises(UnknownColumnError, match="single quotes"):
        validator.validate('SELECT Name FROM Teacher WHERE Name = "Ada Byrne"')


@pytest.mark.parametrize(
    "sql", ["SELECT Name FROM Teacher;", "SELECT Name FROM Teacher -- all teachers"]
)
def test_validated_sql_runs_inside_the_row_limit_wrapper(
    validator: SqlValidator, database: Database, sql: str
):
    executor = QueryExecutor(database, max_rows=10, timeout_ms=1000)
    assert executor.execute(validator.validate(sql)).row_count == 3


def test_positional_order_by_runs_as_written(
    validator: SqlValidator, database: Database
):
    executor = QueryExecutor(database, max_rows=10, timeout_ms=1000)
    result = executor.execute(validator.validate(POSITIONAL_ORDER))
    assert result.rows == [["Ada Byrne", 2], ["Ben Clarke", 1], ["Cara Diaz", 0]]


def test_rowid_runs_after_validation(validator: SqlValidator, database: Database):
    executor = QueryExecutor(database, max_rows=10, timeout_ms=1000)
    result = executor.execute(validator.validate("SELECT rowid, Name FROM Teacher"))
    assert result.rows[0] == [1, "Ada Byrne"]
