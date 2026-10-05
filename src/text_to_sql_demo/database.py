import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

type SqlValue = int | float | str | None

USER_TABLES = (
    "SELECT name FROM pragma_table_list "
    "WHERE schema = 'main' AND type IN ('table', 'virtual') "
    "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name"
)


def to_sql_value(value: object) -> SqlValue:
    """Convert a value read from SQLite into a JSON-safe value.

    Parameters
    ----------
    value
        A value from a result row.

    Returns
    -------
    SqlValue
        The value itself, or ``"<N bytes>"`` for a blob.
    """
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if value is None or isinstance(value, int | float | str):
        return value
    return str(value)


def quote_identifier(name: str) -> str:
    """Quote a table or column name for SQLite."""
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def table_names(connection: sqlite3.Connection) -> list[str]:
    """Return the names of the user tables, sorted.

    SQLite's own tables and the shadow tables of virtual tables are left out.
    """
    return [row[0] for row in connection.execute(USER_TABLES)]


class Database:
    """A SQLite file opened read-only, one connection per use.

    Get one from ``DatabaseRegistry.get``, which checks the id and the file.

    Parameters
    ----------
    database_id
        The id, the file name without ``.sqlite``.
    path
        The SQLite file.
    """

    def __init__(self, database_id: str, path: Path) -> None:
        self.id = database_id
        self.path = path
        self._uri = f"{path.resolve().as_uri()}?mode=ro"

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a new read-only connection and close it on exit.

        The file is opened with ``mode=ro`` and ``PRAGMA query_only = ON``,
        and SQLite's defensive mode is on when the ``sqlite3`` module has
        ``SQLITE_DBCONFIG_DEFENSIVE``.

        Yields
        ------
        sqlite3.Connection
            A connection for the caller alone.
        """
        connection = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
        try:
            defensive = getattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", None)
            if defensive is not None:
                connection.setconfig(defensive, True)
            connection.execute("PRAGMA query_only = ON")
            yield connection
        finally:
            connection.close()
