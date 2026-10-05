from typing import Annotated, Literal, Self

from nodestep import Tool, ToolContext, interrupt, tool
from nodestep.chat import HumanMessage, ToolMessage
from pydantic import BaseModel, Field, ValidationError, model_validator

from text_to_sql_demo.charts import Axis, ChartSpec
from text_to_sql_demo.database import Database
from text_to_sql_demo.errors import AskUserAloneError, ChartError, SessionContextError
from text_to_sql_demo.execution import QueryExecutor, SqlResult
from text_to_sql_demo.frames import Proposal
from text_to_sql_demo.safety import SqlValidator
from text_to_sql_demo.schema import DatabaseSchema, SchemaInspector, TableDescription

CLARIFY = "clarify"


class DatabaseSession:
    """The database of one agent run, passed to the graph as ``context=``.

    The tools read it as ``ctx.context``. It is never stored in the state,
    so every run and every resume builds a new one.

    Parameters
    ----------
    database
        A database from ``DatabaseRegistry.get``.
    max_rows, timeout_ms
        The query limits, as in ``QueryExecutor``.
    """

    def __init__(self, database: Database, *, max_rows: int, timeout_ms: int) -> None:
        self.database = database
        self.inspector = SchemaInspector(database)
        self.executor = QueryExecutor(
            database, max_rows=max_rows, timeout_ms=timeout_ms
        )
        self._schema: DatabaseSchema | None = None
        self._validator: SqlValidator | None = None

    def schema(self) -> DatabaseSchema:
        """Return the schema, read on the first call."""
        if self._schema is None:
            self._schema = self.inspector.inspect()
        return self._schema

    def validator(self) -> SqlValidator:
        """Return a validator for the schema."""
        if self._validator is None:
            self._validator = SqlValidator(self.schema())
        return self._validator


class TableSummary(BaseModel):
    """A table's name, row count and column names."""

    name: str
    row_count: int
    columns: list[str]


class TableList(BaseModel):
    """Every table of the database."""

    database: str
    tables: list[TableSummary]


class ClarificationRequest(BaseModel):
    """The payload of the ``clarify`` interrupt: a question with proposals."""

    question: str = Field(min_length=1)
    proposals: list[Proposal] = Field(min_length=2, max_length=4)
    allow_free_text: bool

    @model_validator(mode="after")
    def _distinct_ids(self) -> Self:
        ids = [proposal.id for proposal in self.proposals]
        if len(set(ids)) != len(ids):
            raise ValueError(f"proposal ids must be distinct, got {ids}")
        return self


class ClarificationAnswer(BaseModel):
    """The user's answer: the chosen proposal, or free text with no proposal."""

    answer: str
    proposal: Proposal | None


class FollowUps(BaseModel):
    """Follow-up questions the user may ask next and has not asked yet."""

    questions: list[str] = Field(max_length=3)


def session_of(ctx: ToolContext) -> DatabaseSession:
    """Return the ``DatabaseSession`` of the run.

    Raises
    ------
    ContextNotProvidedError
        If the run was started without ``context=``.
    SessionContextError
        If the context is not a ``DatabaseSession``.
    """
    session = ctx.context
    if not isinstance(session, DatabaseSession):
        raise SessionContextError(
            f"text-to-sql-demo tools need a DatabaseSession as context=, got {type(session).__name__}"
        )
    return session


@tool
def list_tables(ctx: ToolContext) -> TableList:
    """List the tables of the database with their row counts and column names.

    Call it first, then describe_table for the tables the question needs.
    """
    schema = session_of(ctx).schema()
    return TableList(
        database=schema.database,
        tables=[
            TableSummary(
                name=table.name,
                row_count=table.row_count,
                columns=[column.name for column in table.columns],
            )
            for table in schema.tables
        ],
    )


@tool
def describe_table(table: str, ctx: ToolContext) -> TableDescription:
    """Describe one table: columns, types, keys and a few sample values per column.

    Parameters
    ----------
    table
        The table name, as listed by list_tables.
    """
    return session_of(ctx).inspector.describe(table)


@tool
def run_sql(sql: str, ctx: ToolContext) -> SqlResult:
    """Run one read-only SQLite query and return its columns and rows.

    Only a single SELECT (or WITH ... SELECT) statement is allowed. The rows
    are capped; truncated tells whether more rows were left out. When the
    query is rejected or fails, the error says why: fix the query and try
    again.

    Parameters
    ----------
    sql
        The query, in SQLite syntax. Write text values in single quotes.
    """
    session = session_of(ctx)
    return session.executor.execute(session.validator().validate(sql))


@tool
def make_chart(
    kind: Literal["bar", "line"],
    title: str,
    x: str,
    y: str,
    ctx: ToolContext,
    x_label: str | None = None,
    y_label: str | None = None,
) -> ChartSpec:
    """Draw a chart of the most recent successful run_sql result.

    Call it in a later step than the run_sql call it draws. Use a line chart
    for values over time and a bar chart to compare categories.

    Parameters
    ----------
    kind
        "bar" or "line".
    title
        A short chart title.
    x
        The result column for the x axis.
    y
        The result column for the y axis; it must hold numbers.
    x_label, y_label
        Axis labels; by default the column names in plain words.
    """
    result = last_result(ctx)
    for column in (x, y):
        if column not in result.columns:
            raise ChartError(
                f"The last result has no column {column!r}. "
                f"Columns: {', '.join(result.columns)}."
            )
    x_index, y_index = result.columns.index(x), result.columns.index(y)
    values = [row[y_index] for row in result.rows]
    if not all(value is None or isinstance(value, int | float) for value in values):
        raise ChartError(
            f"The column {y!r} does not hold numbers, so it cannot be the y axis."
        )
    return ChartSpec(
        kind=kind,
        title=title,
        x=Axis(field=x, label=x_label or plain_label(x)),
        y=Axis(field=y, label=y_label or plain_label(y)),
        data=[{x: row[x_index], y: row[y_index]} for row in result.rows],
    )


@tool
async def ask_user(
    question: str,
    proposals: Annotated[list[Proposal], Field(min_length=2, max_length=4)],
    ctx: ToolContext,
    allow_free_text: bool = True,
) -> ClarificationAnswer:
    """Ask the user a question when the request can be read in several ways.

    Offer 2 to 4 proposals, each with a short label and a one-line
    description of what it would compute. Call it on its own, without other
    tools in the same step. The answer names the chosen proposal, or holds
    the user's own words when they wrote free text.

    Parameters
    ----------
    question
        One short question.
    proposals
        2 to 4 answers with distinct ids.
    allow_free_text
        Whether the user may answer in their own words.
    """
    pending = getattr(ctx.state, "tool_calls", None) or []
    if len(pending) > 1:
        raise AskUserAloneError(
            "Call ask_user on its own, without other tools in the same step. "
            "The other calls of this step ran normally."
        )
    request = ClarificationRequest(
        question=question, proposals=proposals, allow_free_text=allow_free_text
    )
    answer = str(interrupt(request.model_dump(), id=CLARIFY))
    chosen = next((p for p in request.proposals if p.id == answer), None)
    return ClarificationAnswer(answer=answer, proposal=chosen)


def question_key(text: str) -> str:
    """Return the form of a question used to tell whether it was asked before.

    Case, repeated spaces and punctuation at the end are ignored, so
    ``"Revenue per  month?"`` and ``"revenue per month"`` give the same key.

    Parameters
    ----------
    text
        The question.

    Returns
    -------
    str
    """
    return " ".join(text.split()).casefold().rstrip("?.! ")


def asked_questions(ctx: ToolContext) -> set[str]:
    """Return the keys of the user's messages in the conversation so far."""
    return {
        question_key(message.content or "")
        for message in getattr(ctx.state, "messages", None) or []
        if isinstance(message, HumanMessage)
    }


@tool
def suggest_follow_ups(
    questions: Annotated[list[str], Field(min_length=2, max_length=3)],
    ctx: ToolContext,
) -> FollowUps:
    """Offer 2 or 3 short follow-up questions the user may ask next.

    Call it once per answer, before the final text. Questions the user
    already asked in this conversation are left out.

    Parameters
    ----------
    questions
        2 or 3 questions, each answerable from this database.
    """
    asked = asked_questions(ctx)
    return FollowUps(
        questions=[
            question for question in questions if question_key(question) not in asked
        ]
    )


TOOLS: list[Tool] = [
    list_tables,
    describe_table,
    run_sql,
    make_chart,
    ask_user,
    suggest_follow_ups,
]


def last_result(ctx: ToolContext) -> SqlResult:
    """Return the most recent successful ``run_sql`` result of the conversation.

    Raises
    ------
    ChartError
        If no ``run_sql`` call has succeeded yet.
    """
    for message in reversed(getattr(ctx.state, "messages", None) or []):
        if isinstance(message, ToolMessage) and message.name == run_sql.name:
            try:
                return SqlResult.model_validate_json(message.content or "")
            except ValidationError:
                continue
    raise ChartError(
        "There is no query result to draw yet. Call run_sql first, then "
        "make_chart in a later step."
    )


def plain_label(column: str) -> str:
    """Return a column name as an axis label: ``"order_count"`` → ``"Order count"``."""
    return column.replace("_", " ").capitalize()
