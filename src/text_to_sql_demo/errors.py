from nodestep import GraphConfigError


class AppError(Exception):
    """Base class for every error text-to-sql-demo raises on purpose."""


class DatabaseNotFoundError(AppError):
    """No database with the requested id is in the databases folder."""


class DatabaseUnreadableError(AppError):
    """SQLite opened the database file but could not read a part of it."""


class ThreadNotFoundError(AppError):
    """No thread with the requested id is stored."""


class TurnNotFoundError(AppError):
    """The thread shows no turn with the requested id."""


class TurnNotEditableError(AppError):
    """The turn is an assistant turn or an answer, so it has no versions."""


class VersionNotFoundError(AppError):
    """The turn has no version with the requested index."""


class RunActiveError(AppError):
    """A run is already streaming on the thread."""


class ClarificationPendingError(AppError):
    """The thread waits for an answer, so it takes no new message."""


class InvalidAnswerError(AppError):
    """The answer is no proposal id, and the question allows no free text."""


class DatabaseRequiredError(AppError):
    """A chat request for a new thread names no database."""


class DatabaseMismatchError(AppError):
    """A request names another database than the one its thread is bound to."""


class ClarificationNotPendingError(AppError):
    """A resume request names no open question of the thread."""


class MemoryNotFoundError(AppError):
    """No memory with the requested key is stored."""


class ScriptExhaustedError(AppError):
    """A ``ScriptedAgent`` was called more often than it has scripts."""


class BranchNotFoundError(AppError):
    """The agent has no record of the branch a call names."""


class TurnPositionNotFoundError(AppError):
    """The agent has no record of the state before a turn, so it cannot edit it."""


class SessionContextError(AppError, GraphConfigError):
    """A run's ``context=`` is not a ``DatabaseSession``.

    It is a ``GraphConfigError``, so it fails the run instead of reaching the
    model as a tool error.
    """


class ChartError(AppError):
    """A chart cannot be drawn from the last query result. The message is for the model."""


class AskUserAloneError(AppError):
    """``ask_user`` was called together with other tools. The message is for the model."""


class SubAgentsAloneError(AppError):
    """``analyze_topics`` was called together with other tools. The message is for the model."""


class SqlError(AppError):
    """A query was rejected or failed. The message is written for the model to act on."""


class SqlSyntaxError(SqlError):
    """The SQL text could not be parsed."""


class QueryTooComplexError(SqlError):
    """The SQL text is too long or nested too deeply to check."""


class UnsupportedSyntaxError(SqlError):
    """The query uses bind parameters or hex literals."""


class MultipleStatementsError(SqlError):
    """The SQL text holds more than one statement."""


class StatementNotAllowedError(SqlError):
    """The statement is not a ``SELECT`` or ``WITH ... SELECT``."""


class ForbiddenOperationError(SqlError):
    """A write, schema change, ``PRAGMA`` or disallowed function appears in the query."""


class UnknownTableError(SqlError):
    """The query names a table that is not in the database schema."""


class UnknownColumnError(SqlError):
    """The query names a column that is not in the referenced tables."""


class QueryTimeoutError(SqlError):
    """The query ran longer than the time limit and was stopped."""


class QueryFailedError(SqlError):
    """SQLite raised an error while running the query."""
