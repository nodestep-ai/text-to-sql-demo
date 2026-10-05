from collections.abc import AsyncGenerator, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import Receive, Scope, Send

from text_to_sql_demo.agent import ChatAgent
from text_to_sql_demo.errors import (
    AppError,
    ClarificationNotPendingError,
    ClarificationPendingError,
    DatabaseMismatchError,
    DatabaseNotFoundError,
    DatabaseRequiredError,
    DatabaseUnreadableError,
    InvalidAnswerError,
    MemoryNotFoundError,
    RunActiveError,
    ThreadNotFoundError,
    TurnNotEditableError,
    TurnNotFoundError,
    VersionNotFoundError,
)
from text_to_sql_demo.frames import ClarificationFrame, Frame, RunFrame, encode_frame
from text_to_sql_demo.memory import MemoryEntry, MemoryList, MemoryStore, MemoryText
from text_to_sql_demo.registry import DatabaseInfo, DatabaseRegistry
from text_to_sql_demo.runs import Run, RunRegistry, RunResult
from text_to_sql_demo.schema import DatabaseSchema, SchemaInspector
from text_to_sql_demo.settings import ServerSettings
from text_to_sql_demo.threads import (
    StoredTurn,
    ThreadDetail,
    ThreadIndex,
    ThreadRecord,
    ThreadSummary,
    new_id,
    thread_title,
)


class ErrorBody(BaseModel):
    """The JSON body of an error answer.

    ``detail`` explains the error to an API client; ``code`` names the kind
    of error, so a client can tell errors with the same status apart.
    """

    detail: str
    code: str


ERRORS: dict[type[AppError], tuple[int, str]] = {
    ThreadNotFoundError: (404, "thread_not_found"),
    DatabaseNotFoundError: (404, "database_not_found"),
    TurnNotFoundError: (404, "turn_not_found"),
    VersionNotFoundError: (404, "version_not_found"),
    MemoryNotFoundError: (404, "memory_not_found"),
    ClarificationNotPendingError: (409, "clarification_not_pending"),
    ClarificationPendingError: (409, "clarification_pending"),
    DatabaseMismatchError: (409, "database_mismatch"),
    RunActiveError: (409, "run_active"),
    DatabaseRequiredError: (422, "database_required"),
    InvalidAnswerError: (422, "invalid_answer"),
    TurnNotEditableError: (422, "turn_not_editable"),
    DatabaseUnreadableError: (500, "database_unreadable"),
}


class ChatRequest(BaseModel):
    """Body of ``POST /api/chat``.

    ``thread_id`` is null for a new thread, which then needs ``database``.
    An existing thread keeps its own database, so ``database`` may be null.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    thread_id: str | None
    database: str | None = Field(default=None, min_length=1)
    message: str = Field(min_length=1)


class ResumeRequest(BaseModel):
    """Body of ``POST /api/threads/{thread_id}/resume``.

    ``answer`` is a proposal id, or free text when the question allows it.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class EditRequest(BaseModel):
    """Body of ``POST /api/threads/{thread_id}/turns/{turn_id}/edit``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1)


class ChatService:
    """Runs the agent for a request and records the turns in the thread index.

    Every check happens before anything is written or streamed. A thread has
    at most one active run.

    Parameters
    ----------
    agent
        The agent that answers.
    threads
        Where threads and turns are stored.
    registry
        The databases a thread can be bound to.
    """

    def __init__(
        self, agent: ChatAgent, threads: ThreadIndex, registry: DatabaseRegistry
    ) -> None:
        self._agent = agent
        self._threads = threads
        self._registry = registry
        self._runs = RunRegistry()

    async def chat(self, request: ChatRequest) -> Run:
        """Record the user turn and start the run that answers it.

        Raises
        ------
        DatabaseRequiredError
            If ``request`` starts a new thread without a database.
        ThreadNotFoundError
            If ``request.thread_id`` is set and unknown.
        DatabaseMismatchError
            If ``request.database`` is not the database of the thread.
        DatabaseNotFoundError
            If the database is not in the databases folder.
        RunActiveError
            If a run is active on the thread.
        ClarificationPendingError
            If the thread waits for the answer to a clarification.
        """
        if request.thread_id is None:
            database = self._new_thread_database(request)
            title = thread_title(request.message)
            thread_id = self._threads.create(title, database)
        else:
            record = self._threads.record(request.thread_id)
            database = self._thread_database(record, request.database)
            self._runs.ensure_idle(record.id)
            if (pending := self._open_question(record)) is not None:
                raise ClarificationPendingError(
                    f"Thread {record.id} waits for the answer to {pending[1].key!r}. "
                    f"Answer it with POST /api/threads/{record.id}/resume or stop "
                    "it first."
                )
            thread_id = record.id
        turn = self._threads.add_user_turn(thread_id, request.message)
        return self._start(
            thread_id,
            database,
            turn,
            self._agent.run(
                thread_id=thread_id,
                database=database,
                branch=turn.branch,
                turn_id=turn.id,
                message=request.message,
            ),
        )

    async def resume(self, thread_id: str, request: ResumeRequest) -> Run:
        """Record the answer as a user turn and start the run that continues.

        Raises
        ------
        ThreadNotFoundError
            If the thread is unknown.
        DatabaseNotFoundError
            If the thread's database is no longer in the databases folder.
        RunActiveError
            If a run is active on the thread.
        ClarificationNotPendingError
            If the thread does not wait for the answer to ``request.key``.
        InvalidAnswerError
            If the answer is no proposal id and free text is not allowed.
        """
        record = self._threads.record(thread_id)
        database = self._thread_database(record, None)
        self._runs.ensure_idle(thread_id)
        pending = self._open_question(record)
        if pending is None:
            raise ClarificationNotPendingError(
                f"Thread {thread_id} has no open question with key {request.key!r}."
            )
        head, question = pending
        if question.key != request.key:
            raise ClarificationNotPendingError(
                f"Thread {thread_id} has no open question with key {request.key!r}; "
                f"it waits for {question.key!r}."
            )
        if question.proposal(request.answer) is None and not question.allow_free_text:
            ids = ", ".join(repr(proposal.id) for proposal in question.proposals)
            raise InvalidAnswerError(
                f"Question {question.key!r} takes one of the proposals {ids}, "
                "not free text."
            )
        self._threads.set_status(thread_id, head.node, "completed")
        turn = self._threads.add_user_turn(
            thread_id, request.answer, answers=request.key
        )
        return self._start(
            thread_id,
            database,
            turn,
            self._agent.resume(
                thread_id=thread_id,
                database=database,
                branch=turn.branch,
                turn_id=turn.id,
                key=request.key,
                answer=request.answer,
            ),
        )

    async def edit(self, thread_id: str, turn_id: str, request: EditRequest) -> Run:
        """Store a new version of a user turn and start the run that answers it.

        Raises
        ------
        ThreadNotFoundError
            If the thread is unknown.
        DatabaseNotFoundError
            If the thread's database is no longer in the databases folder.
        RunActiveError
            If a run is active on the thread.
        TurnNotFoundError
            If the thread shows no turn ``turn_id``.
        TurnNotEditableError
            If the turn is an assistant turn or an answer.
        """
        record = self._threads.record(thread_id)
        database = self._thread_database(record, None)
        self._runs.ensure_idle(thread_id)
        version = self._threads.add_version(thread_id, turn_id, request.message)
        return self._start(
            thread_id,
            database,
            version,
            self._agent.edit(
                thread_id=thread_id,
                database=database,
                branch=version.branch,
                turn_id=version.id,
                message=request.message,
            ),
        )

    async def stop(self, thread_id: str) -> None:
        """Stop the active run, or close the open question, of a thread.

        Without either, nothing happens. While the question is closed, the
        thread is busy: chat, edit, resume and version requests answer 409.

        Raises
        ------
        ThreadNotFoundError
            If the thread is unknown.
        """
        record = self._threads.record(thread_id)
        if await self._runs.stop(thread_id):
            return
        if (pending := self._open_question(record)) is None:
            return
        head = pending[0]
        async with self._runs.closing(thread_id):
            self._threads.set_status(thread_id, head.node, "stopped")
            await self._agent.cancel(
                thread_id=thread_id, database=record.database, branch=head.branch
            )

    def select_version(self, thread_id: str, turn_id: str, index: int) -> ThreadDetail:
        """Show version ``index`` of a user turn and return the thread.

        Raises
        ------
        ThreadNotFoundError
            If the thread is unknown.
        RunActiveError
            If a run is active on the thread.
        TurnNotFoundError
            If the thread shows no turn ``turn_id``.
        TurnNotEditableError
            If the turn is an assistant turn or an answer.
        VersionNotFoundError
            If the turn has no version ``index``.
        """
        record = self._threads.record(thread_id)
        self._runs.ensure_idle(record.id)
        return self._threads.select_version(thread_id, turn_id, index)

    def _start(
        self,
        thread_id: str,
        database: str,
        turn: StoredTurn,
        frames: AsyncGenerator[Frame, None],
    ) -> Run:
        async def cancel() -> None:
            await self._agent.cancel(
                thread_id=thread_id, database=database, branch=turn.branch
            )

        def record(result: RunResult) -> None:
            self._threads.add_assistant_turn(
                thread_id, turn.node, result.text, result.frames, result.status
            )

        return self._runs.start(
            thread_id,
            RunFrame(thread_id=thread_id, run_id=new_id(), database=database),
            frames,
            on_cancel=cancel,
            on_end=record,
        )

    def _new_thread_database(self, request: ChatRequest) -> str:
        if request.database is None:
            raise DatabaseRequiredError(
                "A new thread needs a database. Set 'database' to an id from "
                "GET /api/databases."
            )
        return self._registry.get(request.database).id

    def _thread_database(self, record: ThreadRecord, requested: str | None) -> str:
        if requested not in (None, record.database):
            raise DatabaseMismatchError(
                f"Thread {record.id} is bound to database {record.database!r}, "
                f"not {requested!r}. Start a new thread for that database."
            )
        return self._registry.get(record.database).id

    @staticmethod
    def _open_question(
        record: ThreadRecord,
    ) -> tuple[StoredTurn, ClarificationFrame] | None:
        head = record.head()
        if head is None or head.status != "waiting":
            return None
        questions = [f for f in head.frames if isinstance(f, ClarificationFrame)]
        return (head, questions[-1]) if questions else None


class RunResponse(StreamingResponse):
    """The server-sent events of one run.

    When the client goes away before ``done``, the run is stopped. The
    response ends once the run has recorded its turn.
    """

    def __init__(self, run: Run) -> None:
        super().__init__(
            self._encode(run),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
        self.run = run

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.run.stop()

    @staticmethod
    async def _encode(run: Run) -> AsyncGenerator[str, None]:
        async for frame in run.events():
            yield encode_frame(frame)


def create_app(settings: ServerSettings, agent: ChatAgent) -> FastAPI:
    """Build the text-to-sql-demo web app.

    Parameters
    ----------
    settings
        Where the databases, the thread index, the memory and the built web
        app are. The databases folder is read again on every request. The web
        app is served at ``/`` when ``settings.web_dist`` exists.
    agent
        Answers chat, resume and edit requests.

    Returns
    -------
    FastAPI
        The API under ``/api`` plus the web app.
    """
    registry = DatabaseRegistry(settings.databases_dir)
    threads = ThreadIndex(settings.data_dir / "threads.json")
    service = ChatService(agent, threads, registry)
    memories = MemoryStore(settings.memory_folder)
    app = FastAPI(
        title="text-to-sql-demo", docs_url=None, redoc_url=None, openapi_url=None
    )
    for error_type, (status, code) in ERRORS.items():
        app.add_exception_handler(error_type, detail_response(status, code))

    @app.get("/api/databases")
    def database_list() -> list[DatabaseInfo]:
        return registry.databases()

    @app.get("/api/databases/{database_id}/schema")
    def schema(database_id: str) -> DatabaseSchema:
        return SchemaInspector(registry.get(database_id)).inspect()

    @app.get("/api/threads")
    def thread_list() -> list[ThreadSummary]:
        return threads.summaries()

    @app.get("/api/threads/{thread_id}")
    def thread_detail(thread_id: str) -> ThreadDetail:
        return threads.get(thread_id)

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> RunResponse:
        return RunResponse(await service.chat(request))

    @app.post("/api/threads/{thread_id}/resume")
    async def resume(thread_id: str, request: ResumeRequest) -> RunResponse:
        return RunResponse(await service.resume(thread_id, request))

    @app.post("/api/threads/{thread_id}/turns/{turn_id}/edit")
    async def edit(thread_id: str, turn_id: str, request: EditRequest) -> RunResponse:
        return RunResponse(await service.edit(thread_id, turn_id, request))

    @app.post("/api/threads/{thread_id}/turns/{turn_id}/versions/{index}")
    async def select_version(thread_id: str, turn_id: str, index: int) -> ThreadDetail:
        return service.select_version(thread_id, turn_id, index)

    @app.post("/api/threads/{thread_id}/stop", status_code=204)
    async def stop(thread_id: str) -> Response:
        await service.stop(thread_id)
        return Response(status_code=204)

    @app.get("/api/memories")
    def memory_list() -> MemoryList:
        return memories.entries()

    @app.post("/api/memories", status_code=201)
    def memory_add(text: MemoryText) -> MemoryEntry:
        return memories.add(text)

    @app.put("/api/memories/{key:path}")
    def memory_edit(key: str, text: MemoryText) -> MemoryEntry:
        return memories.edit(key, text)

    @app.delete("/api/memories/{key:path}", status_code=204)
    def memory_delete(key: str) -> Response:
        memories.delete(key)
        return Response(status_code=204)

    if settings.web_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dist, html=True), name="web")
    return app


def detail_response(
    status: int, code: str
) -> Callable[[Request, Exception], JSONResponse]:
    """Return an exception handler that answers ``status`` with an ``ErrorBody``."""

    def respond(request: Request, error: Exception) -> JSONResponse:
        body = ErrorBody(detail=str(error), code=code)
        return JSONResponse(status_code=status, content=body.model_dump())

    return respond
