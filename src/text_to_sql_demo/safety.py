import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError, TokenError
from sqlglot.generators.sqlite import SQLiteGenerator
from sqlglot.optimizer.qualify import qualify

from text_to_sql_demo.errors import (
    ForbiddenOperationError,
    MultipleStatementsError,
    QueryTooComplexError,
    SqlSyntaxError,
    StatementNotAllowedError,
    UnknownColumnError,
    UnknownTableError,
    UnsupportedSyntaxError,
)
from text_to_sql_demo.schema import DatabaseSchema, Table

MAX_SQL_LENGTH = 20_000
ROWID_ALIASES = ("rowid", "oid", "_rowid_")

FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)


class SqlLayoutGenerator(SQLiteGenerator):
    """sqlglot's pretty SQLite layout, with parenthesised expressions on one line.

    A parenthesised subquery keeps sqlglot's layout over several lines.
    """

    def paren_sql(self, expression: exp.Paren) -> str:
        if isinstance(expression.this, exp.Query):
            return super().paren_sql(expression)
        return f"({self.sql(expression, 'this')})"


class SqlValidator:
    """Checks model-written SQL against the database schema.

    Parameters
    ----------
    schema
        The introspected schema that tables and columns are checked against.
    """

    def __init__(self, schema: DatabaseSchema) -> None:
        self._schema = schema
        self._mapping: dict[str, object] = {
            table.name: self._columns(table) for table in schema.tables
        }

    def validate(self, sql: str) -> str:
        """Validate one read-only query and return it pretty-printed for SQLite.

        The returned SQL is generated from the validated syntax tree, so what
        runs, and what is shown, is what was checked. It is laid out over
        several lines with keywords and function names in upper case; an
        expression in parentheses stays on one line.
        Comments become block comments and a trailing semicolon is dropped.
        SQLite names a result column without an alias after its text, so the
        name of such an expression column follows the layout.

        Parameters
        ----------
        sql
            One ``SELECT`` or ``WITH ... SELECT`` statement.

        Returns
        -------
        str
            The statement as formatted SQLite SQL.

        Raises
        ------
        QueryTooComplexError
            If the text is longer than ``MAX_SQL_LENGTH`` or nested too deeply.
        SqlSyntaxError
            If the text is empty or cannot be parsed.
        UnsupportedSyntaxError
            If the query has bind parameters or hex literals.
        MultipleStatementsError
            If the text holds more than one statement.
        StatementNotAllowedError
            If the statement is not a ``SELECT``.
        ForbiddenOperationError
            If a write, schema change or ``PRAGMA`` appears anywhere in it.
        UnknownTableError
            If a table is not in the schema, has a schema prefix or is a
            table-valued function.
        UnknownColumnError
            If a column is not in the tables it reads from.
        """
        if len(sql) > MAX_SQL_LENGTH:
            raise QueryTooComplexError(
                f"The query is {len(sql)} characters long; the limit is {MAX_SQL_LENGTH}. "
                "Write a shorter query, for example with a JOIN or a range "
                "instead of a long list of values."
            )
        try:
            statement = self._parse(sql)
            self._check_statement(statement)
            self._check_tables(statement)
            self._check_columns(statement)
            return SqlLayoutGenerator(pretty=True, dialect="sqlite").generate(statement)
        except RecursionError as error:
            raise QueryTooComplexError(
                "The query is nested too deeply to check. "
                "Use fewer nested parentheses and subqueries."
            ) from error
        except (SqlglotError, AssertionError) as error:
            raise SqlSyntaxError(f"The query could not be checked: {error}") from error

    @staticmethod
    def _columns(table: Table) -> dict[str, str]:
        columns = {column.name: "TEXT" for column in table.columns}
        known = {name.casefold() for name in columns}
        columns.update({alias: "TEXT" for alias in ROWID_ALIASES if alias not in known})
        return columns

    @staticmethod
    def _parse(sql: str) -> exp.Expr:
        try:
            statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
        except ParseError as error:
            first = error.errors[0] if error.errors else None
            detail = (
                f"{first['description']} at line {first['line']}, column {first['col']}"
                if first
                else str(error)
            )
            raise SqlSyntaxError(f"The SQL could not be parsed: {detail}.") from error
        except TokenError as error:
            raise SqlSyntaxError(
                f"The SQL could not be parsed: {error}. Check quotes and brackets."
            ) from error
        if not statements:
            raise SqlSyntaxError("No SQL statement found. Send one SELECT statement.")
        if len(statements) > 1:
            raise MultipleStatementsError(
                f"Send exactly one statement; found {len(statements)}."
            )
        return statements[0]

    def _check_statement(self, statement: exp.Expr) -> None:
        if not isinstance(statement, exp.Select | exp.SetOperation):
            raise StatementNotAllowedError(
                "Only SELECT statements are allowed, optionally starting with WITH. "
                f"Got {self._operation(statement)}."
            )
        for node in statement.walk():
            if isinstance(node, FORBIDDEN):
                raise ForbiddenOperationError(
                    f"The query contains {self._operation(node)}. "
                    "Only reading with SELECT is allowed."
                )
            if self._is_parameter(node):
                raise UnsupportedSyntaxError(
                    f"The query has the bind parameter {node.sql(dialect='sqlite')}. "
                    "Parameters are not supported. Write values into the query, "
                    "for example WHERE status = 'completed'."
                )
            if isinstance(node, exp.HexString):
                raise UnsupportedSyntaxError(
                    "Hex and blob literals such as 0x10 or x'10' are not supported. "
                    "Write numbers in decimal, for example 16 instead of 0x10."
                )

    @staticmethod
    def _is_parameter(node: exp.Expr) -> bool:
        if isinstance(node, exp.Placeholder | exp.Parameter):
            return True
        return (
            isinstance(node, exp.Identifier)
            and not node.quoted
            and node.name.startswith("$")
        )

    def _check_tables(self, statement: exp.Expr) -> None:
        for table in statement.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                raise UnknownTableError(
                    f"Table-valued functions such as {table.this.sql(dialect='sqlite')} "
                    f"are not allowed. {self._tables_hint()}"
                )
            if table.args.get("db") or table.args.get("catalog"):
                raise UnknownTableError(
                    f"Write table names without a schema prefix: {table.name} "
                    f"instead of {table.sql(dialect='sqlite')}."
                )
            if self._is_cte(table):
                continue
            if self._schema.find(table.name) is None:
                raise UnknownTableError(
                    f"There is no table {table.name!r}. {self._tables_hint()}"
                )

    @staticmethod
    def _is_cte(table: exp.Table) -> bool:
        name = table.name.casefold()
        node = table.parent
        while node is not None:
            with_ = node.args.get("with_")
            if isinstance(with_, exp.With) and any(
                cte.alias_or_name.casefold() == name for cte in with_.expressions
            ):
                return True
            node = node.parent
        return False

    def _check_columns(self, statement: exp.Expr) -> None:
        try:
            qualified = qualify(
                statement.copy(),
                schema=self._mapping,
                dialect="sqlite",
                infer_schema=False,
                validate_qualify_columns=True,
            )
            for having in qualified.find_all(exp.Having):
                for column in having.find_all(exp.Column):
                    if not column.table:
                        raise OptimizeError(
                            f"Column '{column.name}' in HAVING could not be resolved"
                        )
        except OptimizeError as error:
            raise UnknownColumnError(self._column_message(statement, error)) from error
        except (SqlglotError, AssertionError):
            return

    def _column_message(self, statement: exp.Expr, error: OptimizeError) -> str:
        used: dict[str, Table] = {}
        for node in statement.find_all(exp.Table):
            table = self._schema.find(node.name)
            if table is not None:
                used.setdefault(table.name, table)
        listing = ", ".join(
            f"{table.name}({', '.join(column.name for column in table.columns)})"
            for table in used.values()
        )
        message = str(error).rstrip(".") + "."
        message += (
            f" Columns of the tables in this query: {listing}."
            if listing
            else " This query reads no tables."
        )
        if self._has_quoted_text(statement):
            message += (
                " Write text values in single quotes; double quotes name columns."
            )
        return message

    def _has_quoted_text(self, statement: exp.Expr) -> bool:
        known = {
            column.name.casefold()
            for table in self._schema.tables
            for column in table.columns
        }
        return any(
            isinstance(column.this, exp.Identifier)
            and column.this.quoted
            and column.name.casefold() not in known
            for column in statement.find_all(exp.Column)
        )

    def _tables_hint(self) -> str:
        return f"Tables: {', '.join(self._schema.table_names())}."

    @staticmethod
    def _operation(node: exp.Expr) -> str:
        if isinstance(node, exp.Command):
            return str(node.this).upper()
        if isinstance(node, exp.Alias) and isinstance(node.this, exp.Column):
            node = node.this
        if isinstance(node, exp.Column):
            return node.name.upper()
        return node.key.upper()
