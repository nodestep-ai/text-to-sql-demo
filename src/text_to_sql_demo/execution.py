import sqlite3
import time
from collections.abc import Sequence
from contextlib import closing

from pydantic import BaseModel

from text_to_sql_demo.database import Database, SqlValue, table_names, to_sql_value
from text_to_sql_demo.errors import (
    ForbiddenOperationError,
    QueryFailedError,
    QueryTimeoutError,
    SqlError,
    UnknownTableError,
)

FUNCTIONS = frozenset(
    {
        "avg",
        "count",
        "group_concat",
        "max",
        "min",
        "string_agg",
        "sum",
        "total",
        "row_number",
        "rank",
        "dense_rank",
        "percent_rank",
        "cume_dist",
        "ntile",
        "lag",
        "lead",
        "first_value",
        "last_value",
        "nth_value",
        "abs",
        "char",
        "coalesce",
        "concat",
        "concat_ws",
        "format",
        "glob",
        "hex",
        "ifnull",
        "iif",
        "instr",
        "length",
        "like",
        "likelihood",
        "likely",
        "lower",
        "ltrim",
        "nullif",
        "octet_length",
        "printf",
        "quote",
        "random",
        "replace",
        "round",
        "rtrim",
        "sign",
        "soundex",
        "substr",
        "substring",
        "trim",
        "typeof",
        "unhex",
        "unicode",
        "unlikely",
        "upper",
        "date",
        "time",
        "datetime",
        "julianday",
        "unixepoch",
        "strftime",
        "timediff",
        "current_date",
        "current_time",
        "current_timestamp",
        "acos",
        "acosh",
        "asin",
        "asinh",
        "atan",
        "atan2",
        "atanh",
        "ceil",
        "ceiling",
        "cos",
        "cosh",
        "degrees",
        "exp",
        "floor",
        "ln",
        "log",
        "log10",
        "log2",
        "mod",
        "pi",
        "pow",
        "power",
        "radians",
        "sin",
        "sinh",
        "sqrt",
        "tan",
        "tanh",
        "trunc",
        "json",
        "json_array",
        "json_array_length",
        "json_extract",
        "json_group_array",
        "json_group_object",
        "json_object",
        "json_quote",
        "json_type",
        "json_valid",
        "->",
        "->>",
    }
)


class SqlResult(BaseModel):
    """The rows of one query, capped at the row limit."""

    sql: str
    columns: list[str]
    rows: list[list[SqlValue]]
    row_count: int
    truncated: bool
    elapsed_ms: int


class QueryAuthorizer:
    """A SQLite authorizer that allows reading the given tables and calling ``FUNCTIONS``.

    SQLite calls it while it prepares a statement, with names after its own
    resolution, so a CTE or subquery cannot hide what is read. A table that
    is referenced without reading any column, as in ``count(*)``, is
    reported with an empty column name for CTEs and tables alike; that
    reveals no values and is allowed. The first denial is kept as the error
    to report.

    Parameters
    ----------
    tables
        The tables a query may read.
    """

    def __init__(self, tables: Sequence[str]) -> None:
        self._names = list(tables)
        self._tables = {name.casefold() for name in tables}
        self.denial: SqlError | None = None

    def __call__(
        self,
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        """Return ``SQLITE_OK`` for an allowed action, else ``SQLITE_DENY``."""
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and (
            second == "" or (first or "").casefold() in self._tables
        ):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (second or "").casefold() in FUNCTIONS:
            return sqlite3.SQLITE_OK
        if self.denial is None:
            self.denial = self._denial(action, first, second)
        return sqlite3.SQLITE_DENY

    def _denial(self, action: int, first: str | None, second: str | None) -> SqlError:
        if action == sqlite3.SQLITE_READ:
            return UnknownTableError(
                f"The query reads {first}, which is not a table of this database. "
                f"Tables: {', '.join(self._names)}."
            )
        if action == sqlite3.SQLITE_FUNCTION:
            return ForbiddenOperationError(
                f"The query calls the function {second}, which is not allowed. "
                "Use standard SQL functions such as count, sum, round, substr or strftime."
            )
        if action == sqlite3.SQLITE_PRAGMA:
            return ForbiddenOperationError(
                f"The query uses PRAGMA {first}, which is not allowed. "
                "Only reading tables with SELECT is allowed."
            )
        return ForbiddenOperationError(
            "The query needs a SQLite operation that is not allowed. "
            "Only reading tables with SELECT is allowed."
        )


class QueryExecutor:
    """Runs validated queries with a row limit, a time limit and size limits.

    Every query runs on its own read-only connection to ``database``. The
    tables it may read are the user tables of that connection; see
    ``QueryAuthorizer``.

    Parameters
    ----------
    database
        A database from ``DatabaseRegistry.get``.
    max_rows
        Most rows returned; one more row is read to detect truncation.
    timeout_ms
        Time limit for one query in milliseconds.
    """

    progress_interval = 1000
    max_value_bytes = 1_000_000
    max_result_bytes = 2_000_000

    def __init__(self, database: Database, *, max_rows: int, timeout_ms: int) -> None:
        self._database = database
        self._max_rows = max_rows
        self._timeout_ms = timeout_ms

    def execute(self, sql: str) -> SqlResult:
        """Run ``sql`` as ``SELECT * FROM (sql) LIMIT max_rows + 1``.

        While the query runs, SQLite may read only the tables of the
        database, call only allowed functions and build values of at most
        ``max_value_bytes``.
        The result stops at ``max_result_bytes``.

        Parameters
        ----------
        sql
            A query returned by ``SqlValidator.validate``.

        Returns
        -------
        SqlResult
            At most ``max_rows`` rows; ``truncated`` tells whether rows were
            left out because of the row limit or the size limit.

        Raises
        ------
        UnknownTableError
            If the query reads a table that is not in the database.
        ForbiddenOperationError
            If the query calls a function that is not allowed or needs any
            other operation than reading.
        QueryTimeoutError
            If the query runs longer than the time limit.
        QueryFailedError
            If SQLite raises any other error, a value is too long or the
            first row alone is larger than the size limit.
        """
        wrapped = f"SELECT * FROM ({sql}) LIMIT {self._max_rows + 1}"
        with self._database.connect() as connection:
            authorizer = QueryAuthorizer(table_names(connection))
            started = time.monotonic()
            deadline = started + self._timeout_ms / 1000
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, self.max_value_bytes)
            connection.set_authorizer(authorizer)
            connection.set_progress_handler(
                lambda: int(time.monotonic() > deadline), self.progress_interval
            )
            try:
                with closing(connection.execute(wrapped)) as cursor:
                    columns = [column[0] for column in cursor.description]
                    rows, truncated = self._fetch(cursor)
            except sqlite3.Error as error:
                raise self._failure(error, authorizer) from error
        return SqlResult(
            sql=sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )

    def _fetch(self, cursor: sqlite3.Cursor) -> tuple[list[list[SqlValue]], bool]:
        rows: list[list[SqlValue]] = []
        size = 0
        for row in cursor:
            if len(rows) == self._max_rows:
                return rows, True
            values = [to_sql_value(value) for value in row]
            size += sum(
                len(value.encode()) if isinstance(value, str) else 8 for value in values
            )
            if size > self.max_result_bytes:
                if not rows:
                    raise QueryFailedError(
                        f"The first row is larger than {self.max_result_bytes} bytes. "
                        "Select fewer or shorter columns, for example with substr()."
                    )
                return rows, True
            rows.append(values)
        return rows, False

    def _failure(self, error: sqlite3.Error, authorizer: QueryAuthorizer) -> SqlError:
        if authorizer.denial is not None:
            return authorizer.denial
        name = getattr(error, "sqlite_errorname", None)
        if name == "SQLITE_INTERRUPT":
            return QueryTimeoutError(
                f"The query was stopped after {self._timeout_ms} ms. "
                "Filter or aggregate earlier, or add a LIMIT."
            )
        if name == "SQLITE_TOOBIG":
            return QueryFailedError(
                f"A value in the query is longer than {self.max_value_bytes} bytes. "
                "Select shorter values, for example with substr()."
            )
        return QueryFailedError(f"SQLite rejected the query: {error}")
