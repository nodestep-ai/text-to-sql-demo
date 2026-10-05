import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel

from text_to_sql_demo.database import (
    Database,
    SqlValue,
    quote_identifier,
    table_names,
    to_sql_value,
)
from text_to_sql_demo.errors import DatabaseUnreadableError, UnknownTableError


class Reference(BaseModel):
    """The target of a foreign key."""

    table: str
    column: str


class Column(BaseModel):
    """A table column with its declared type and keys."""

    name: str
    type: str
    primary_key: bool
    references: Reference | None


class Table(BaseModel):
    """A table with its row count and columns."""

    name: str
    row_count: int
    columns: list[Column]


class DatabaseSchema(BaseModel):
    """Every user table of a database, sorted by name."""

    database: str
    tables: list[Table]

    def find(self, name: str) -> Table | None:
        """Return the table called ``name`` (case-insensitive), or None."""
        folded = name.casefold()
        return next((t for t in self.tables if t.name.casefold() == folded), None)

    def table_names(self) -> list[str]:
        """Return the table names in schema order."""
        return [table.name for table in self.tables]


class DescribedColumn(Column):
    """A column plus a few distinct non-null values from the table."""

    samples: list[SqlValue]


class TableDescription(BaseModel):
    """A table with sample values for every column."""

    name: str
    row_count: int
    columns: list[DescribedColumn]


class SchemaInspector:
    """Reads tables, columns, keys and sample values from a database.

    Every call opens its own read-only connection.

    Parameters
    ----------
    database
        A database from ``DatabaseRegistry.get``.
    sample_size
        Most distinct values ``describe`` returns per column.
    """

    def __init__(self, database: Database, *, sample_size: int = 3) -> None:
        self._database = database
        self._sample_size = sample_size

    def inspect(self) -> DatabaseSchema:
        """Return every table except SQLite's internal ones.

        Raises
        ------
        DatabaseUnreadableError
            If SQLite cannot read a part of the file.
        """
        with self._connect() as connection:
            return self._inspect(connection)

    def describe(self, table: str) -> TableDescription:
        """Return one table with sample values.

        Raises
        ------
        UnknownTableError
            If no table is called ``table``; the message lists the tables.
        DatabaseUnreadableError
            If SQLite cannot read a part of the file.
        """
        with self._connect() as connection:
            schema = self._inspect(connection)
            found = schema.find(table)
            if found is None:
                raise UnknownTableError(
                    f"There is no table {table!r}. Tables: {', '.join(schema.table_names())}."
                )
            return TableDescription(
                name=found.name,
                row_count=found.row_count,
                columns=[
                    DescribedColumn(
                        **column.model_dump(),
                        samples=self._samples(connection, found.name, column.name),
                    )
                    for column in found.columns
                ],
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            with self._database.connect() as connection:
                yield connection
        except sqlite3.Error as error:
            raise DatabaseUnreadableError(
                f"Database {self._database.id!r} could not be read: {error}."
            ) from error

    def _inspect(self, connection: sqlite3.Connection) -> DatabaseSchema:
        names = table_names(connection)
        primary_keys = {name: self._primary_key(connection, name) for name in names}
        canonical = {name.casefold(): name for name in names}
        return DatabaseSchema(
            database=self._database.id,
            tables=[
                self._table(connection, name, primary_keys, canonical) for name in names
            ],
        )

    def _table(
        self,
        connection: sqlite3.Connection,
        name: str,
        primary_keys: dict[str, list[str]],
        canonical: dict[str, str],
    ) -> Table:
        references = self._references(connection, name, primary_keys, canonical)
        columns = [
            Column(
                name=column,
                type=declared,
                primary_key=pk > 0,
                references=references.get(column),
            )
            for column, declared, pk in connection.execute(
                "SELECT name, type, pk FROM pragma_table_info(?)", (name,)
            )
        ]
        (row_count,) = connection.execute(
            f"SELECT count(*) FROM {quote_identifier(name)}"
        ).fetchone()
        return Table(name=name, row_count=row_count, columns=columns)

    def _primary_key(self, connection: sqlite3.Connection, name: str) -> list[str]:
        rows = connection.execute(
            "SELECT name FROM pragma_table_info(?) WHERE pk > 0 ORDER BY pk", (name,)
        )
        return [row[0] for row in rows]

    def _references(
        self,
        connection: sqlite3.Connection,
        name: str,
        primary_keys: dict[str, list[str]],
        canonical: dict[str, str],
    ) -> dict[str, Reference]:
        references: dict[str, Reference] = {}
        rows = connection.execute(
            'SELECT "table", "from", "to", seq FROM pragma_foreign_key_list(?)', (name,)
        )
        for target, source, column, seq in rows:
            table = canonical.get(target.casefold(), target)
            if column is None:
                keys = primary_keys.get(table, [])
                if seq >= len(keys):
                    continue
                column = keys[seq]
            references[source] = Reference(table=table, column=column)
        return references

    def _samples(
        self, connection: sqlite3.Connection, table: str, column: str
    ) -> list[SqlValue]:
        rows = connection.execute(
            f"SELECT DISTINCT {quote_identifier(column)} FROM {quote_identifier(table)} "
            f"WHERE {quote_identifier(column)} IS NOT NULL LIMIT ?",
            (self._sample_size,),
        )
        return [to_sql_value(row[0]) for row in rows]
