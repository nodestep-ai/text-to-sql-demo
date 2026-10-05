from collections.abc import AsyncGenerator
from contextlib import aclosing, suppress
from pathlib import Path
from typing import Any, Self

from nodestep import (
    END,
    START,
    AgentState,
    FilesystemStateStore,
    Graph,
    Resume,
    StreamEvent,
    Tool,
    branch,
    model_node,
    tool_runner,
)
from nodestep.chat import AIMessage, Chat, ChatStreamChunk, HumanMessage, ToolMessage
from nodestep.core.stream import FinalEventData, InterruptEventData
from nodestep.middleware import (
    FilesystemSkills,
    LoadedSkill,
    Memory,
    MemoryDeleteResult,
    MemoryListResult,
    MemorySearchResult,
    SkillNotFoundError,
    ToolLimitMiddleware,
)
from nodestep.state import StateStore
from pydantic import BaseModel, ValidationError

from text_to_sql_demo.charts import ChartSpec
from text_to_sql_demo.errors import BranchNotFoundError
from text_to_sql_demo.execution import SqlResult
from text_to_sql_demo.frames import (
    ChartFrame,
    ClarificationFrame,
    FinalFrame,
    Frame,
    SqlResultFrame,
    SuggestionsFrame,
    TokenFrame,
    ToolFrame,
    UsageFrame,
)
from text_to_sql_demo.memory import MemoryStore
from text_to_sql_demo.positions import (
    AgentPositions,
    BranchPosition,
    BranchStatus,
    TurnStart,
)
from text_to_sql_demo.registry import DatabaseRegistry
from text_to_sql_demo.schema import TableDescription
from text_to_sql_demo.settings import ServerSettings
from text_to_sql_demo.subagents import (
    MAX_TOPIC_CALLS,
    TopicFindings,
    TopicResults,
    analyze_topics,
    build_analyst,
    delegate_node,
)
from text_to_sql_demo.threads import MAIN_BRANCH, new_id
from text_to_sql_demo.tools import (
    CLARIFY,
    TOOLS,
    ClarificationRequest,
    DatabaseSession,
    FollowUps,
    TableList,
    ask_user,
    suggest_follow_ups,
)
from text_to_sql_demo.tracing import Tracing

MAX_TOOL_CALLS = 20
MAX_STEPS = 2 * (MAX_TOOL_CALLS + 1) + 1
STREAM_MODES = ["updates", "tokens", "custom"]
STOPPED_CALL = "Not run: the answer ended before this call ran."
TOOL_ERROR_PREFIX = "Tool error: "
HIDDEN_TOOLS = frozenset({ask_user.name, suggest_follow_ups.name})
SHIPPED_SKILLS = Path(__file__).parent / "skills"
MODEL_NODE = "think"
OUTPUTS: dict[str, type[BaseModel]] = {
    "list_tables": TableList,
    "describe_table": TableDescription,
    "run_sql": SqlResult,
    "make_chart": ChartSpec,
    "save_memory": Memory,
    "search_memory": MemorySearchResult,
    "list_memories": MemoryListResult,
    "delete_memory": MemoryDeleteResult,
    "load_skill": LoadedSkill,
    analyze_topics.name: TopicFindings,
}
SYSTEM_PROMPT = """\
You answer questions about a SQLite database by querying it with the tools.

- Start with list_tables, and describe_table for the tables you need, unless \
this conversation already shows them.
- Before you query, call search_memory with the key words of the question. \
The memory holds what the user asked you to remember, such as how to count \
revenue. Follow the memories that match, and when the answer uses one, say so \
in plain words, for example "From memory: revenue counts completed orders only."
- Call save_memory only when the user asks you to remember something, with a \
short key, a short title and the fact as one sentence. Call delete_memory only \
when the user asks you to forget something or not to remember it; find its \
key with search_memory or list_memories first. These requests need no query.
- Base every answer on run_sql results. Write one read-only SELECT in SQLite \
syntax, with text values in single quotes. When a query is rejected or fails, \
read the error, fix the query and run it again.
- For a question with separate parts, such as a report, call analyze_topics \
on its own with one topic per part. Each topic goes to a sub-agent that \
queries this database; write the answer from their findings.
- The skills listed below are instructions for some tasks. Call load_skill \
before a task that one of them covers, and follow it.
- When a chart helps, such as a trend over time or a comparison of a few \
categories, call make_chart in the step after the run_sql call it draws.
- When the question can be read in ways that give different numbers, call \
ask_user on its own with 2 to 4 proposals. Each proposal's description says \
in one line what it would compute.
- Before the final answer to a question about the data, call \
suggest_follow_ups once with 2 or 3 short questions the user could ask next \
and has not asked in this conversation.
- The final answer is short plain text: the result in a few sentences with \
the key numbers. Do not repeat the SQL or the whole table; the user sees both.
"""


def build_graph(
    chat: Chat,
    *,
    state_store: StateStore,
    memory_tools: list[Tool],
    skills: FilesystemSkills,
    analyst: Graph[AgentState],
) -> Graph[AgentState]:
    """Build the text-to-sql-demo agent graph.

    Parameters
    ----------
    chat
        The model: ``OpenAIChat`` for ``text-to-sql-demo serve``, a scripted chat for
        the demo and the tests.
    state_store
        Where the threads are kept.
    memory_tools
        The memory tools, from ``MemoryStore.tools``.
    skills
        The skills, listed in every request of the model node ``think``.
    analyst
        The sub-agent graph that ``analyze_topics`` runs, from
        ``build_analyst``.

    Returns
    -------
    Graph[AgentState]
        A ReAct agent like nodestep's ``build_react_agent``, over ``TOOLS``,
        ``analyze_topics``, the memory tools and ``load_skill``, that streams
        the model's tokens. The node ``think`` calls the model and ``act``
        runs its tool calls; a step whose only call is ``analyze_topics``
        goes to ``delegate`` instead, which runs the sub-agents. A tool error
        reaches the model as a tool message, within a budget of
        ``MAX_TOOL_CALLS`` calls per run, ``analyze_topics`` included and at
        most ``MAX_TOPIC_CALLS`` times; configuration errors still raise.
        Each round of tool calls takes two steps, so ``MAX_STEPS`` lets the
        model make ``MAX_TOOL_CALLS`` calls one at a time, read the denial of
        one more and answer. A model that keeps calling tools after the
        denial ends the run with ``RunLimitExceededError``.
    """
    tools = [*TOOLS, analyze_topics, *memory_tools, *skills.tools()]
    think = model_node(
        MODEL_NODE,
        chat=chat,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        stream=True,
    )
    act = tool_runner("act", tools=tools, tool_errors="return")
    delegate = delegate_node(analyst)
    return Graph(
        AgentState,
        name="text-to-sql-demo",
        max_steps=MAX_STEPS,
        middleware=[
            ToolLimitMiddleware(
                max_calls=MAX_TOOL_CALLS,
                per_tool={analyze_topics.name: MAX_TOPIC_CALLS},
            ),
            skills,
        ],
        state_store=state_store,
    ).flow(
        START >> think,
        think >> branch(next_step, {"act": act, "delegate": delegate, "end": END}),
        act >> think,
        delegate >> think,
    )


def next_step(state: AgentState) -> str:
    """Return where a step goes after the model: ``act``, ``delegate`` or ``end``."""
    if not state.tool_calls:
        return "end"
    if [call.name for call in state.tool_calls] == [analyze_topics.name]:
        return "delegate"
    return "act"


def skill_roots(folder: Path) -> list[Path]:
    """Return where skills are read from: ``SHIPPED_SKILLS``, and ``folder`` when it holds a ``SKILL.md``."""
    own = [folder] if folder.is_dir() and any(folder.rglob("SKILL.md")) else []
    return [SHIPPED_SKILLS, *own]


class FrameTranslator:
    """Turns the stream events of one agent run into text-to-sql-demo frames.

    Parameters
    ----------
    model
        The model name a ``usage`` frame reports when the chunk names none.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self._suggestions: list[str] = []

    def frames(self, event: StreamEvent) -> list[Frame]:
        """Return the frames for ``event``; most events give none or one."""
        if event.mode == "tokens" and isinstance(event.data, ChatStreamChunk):
            return self._chunk(event.data)
        if event.mode == "custom" and isinstance(event.data, TopicResults):
            return [
                SqlResultFrame.from_result(
                    f"{event.data.call_id}/{topic}/{number}", result
                )
                for topic, results in enumerate(event.data.results, start=1)
                for number, result in enumerate(results, start=1)
            ]
        if event.mode == "updates" and isinstance(event.data, dict):
            messages = event.data.get("messages") or []
            return [frame for message in messages for frame in self._message(message)]
        if event.mode == "interrupt" and isinstance(event.data, InterruptEventData):
            return [
                ClarificationFrame(
                    key=key,
                    **ClarificationRequest.model_validate(pending.payload).model_dump(),
                )
                for key, pending in event.data.interrupts.items()
                if pending.id == CLARIFY
            ]
        if event.mode == "final" and isinstance(event.data, FinalEventData):
            final: list[Frame] = [
                FinalFrame(text=event.data.state.get("final_text") or "")
            ]
            if len(self._suggestions) >= 2:
                final.append(SuggestionsFrame(questions=self._suggestions))
            return final
        return []

    def _chunk(self, chunk: ChatStreamChunk) -> list[Frame]:
        frames: list[Frame] = []
        if chunk.content_delta:
            frames.append(TokenFrame(text=chunk.content_delta))
        if chunk.usage:
            frames.append(
                UsageFrame(
                    model=chunk.model or self._model,
                    input_tokens=count(chunk.usage, "input_tokens", "prompt_tokens"),
                    output_tokens=count(
                        chunk.usage, "output_tokens", "completion_tokens"
                    ),
                )
            )
        return frames

    def _message(self, message: Any) -> list[Frame]:
        if isinstance(message, AIMessage):
            return [
                ToolFrame(
                    call_id=call.id,
                    name=call.name,
                    status="started",
                    arguments=call.arguments,
                )
                for call in message.tool_calls
                if call.name not in HIDDEN_TOOLS
            ]
        if isinstance(message, ToolMessage) and message.tool_call_id is not None:
            return self._result(
                message.tool_call_id, message.name or "", message.content
            )
        return []

    def _result(self, call_id: str, name: str, content: str | None) -> list[Frame]:
        if name == suggest_follow_ups.name:
            with suppress(ValidationError):
                self._suggestions = FollowUps.model_validate_json(
                    content or ""
                ).questions
            return []
        if name in HIDDEN_TOOLS:
            return []
        failed = ToolFrame(
            call_id=call_id,
            name=name,
            status="error",
            summary=error_text(content or ""),
        )
        output = OUTPUTS.get(name)
        if output is None:
            return [failed]
        try:
            value = output.model_validate_json(content or "")
        except ValidationError:
            return [failed]
        if isinstance(value, MemoryDeleteResult) and not value.deleted:
            return [
                failed.model_copy(
                    update={"summary": f"No memory has the key {value.key!r}."}
                )
            ]
        finished = ToolFrame(
            call_id=call_id, name=name, status="finished", summary=summary(value)
        )
        if isinstance(value, SqlResult):
            return [finished, SqlResultFrame.from_result(call_id, value)]
        if isinstance(value, ChartSpec):
            return [finished, ChartFrame.from_spec(call_id, value)]
        return [finished]


def count(usage: dict[str, Any], *names: str) -> int:
    """Return the first of ``names`` that ``usage`` holds as a number, else 0."""
    for name in names:
        value = usage.get(name)
        if isinstance(value, int):
            return value
    return 0


def error_text(content: str) -> str:
    """Return a tool error without nodestep's ``Tool error: <type>: `` prefix.

    A ``SkillNotFoundError``, whose message is only the name, says that no
    skill has that name.
    """
    if not content.startswith(TOOL_ERROR_PREFIX):
        return content
    rest = content.removeprefix(TOOL_ERROR_PREFIX)
    kind, separator, message = rest.partition(": ")
    if separator and kind == SkillNotFoundError.__name__:
        return f"No skill is named {message!r}."
    if separator and kind.isidentifier():
        return message
    return rest


def summary(value: BaseModel) -> str:
    """Return the one-line summary a finished tool row shows."""
    if isinstance(value, TableList):
        names = ", ".join(table.name for table in value.tables)
        return f"{len(value.tables)} tables: {names}"
    if isinstance(value, TableDescription):
        return f"{value.name}: {len(value.columns)} columns, {value.row_count} rows"
    if isinstance(value, SqlResult):
        more = ", more left out" if value.truncated else ""
        return f"{value.row_count} rows{more}"
    if isinstance(value, ChartSpec):
        return f"{value.kind} chart"
    if isinstance(value, Memory):
        return value.title
    if isinstance(value, MemorySearchResult):
        titles = ", ".join(memory.title for memory in value.results)
        if len(value.results) == 0:
            return "No match"
        if len(value.results) == 1:
            return f"1 match: {titles}"
        return f"{len(value.results)} matches: {titles}"
    if isinstance(value, MemoryListResult):
        return (
            "1 memory" if len(value.entries) == 1 else f"{len(value.entries)} memories"
        )
    if isinstance(value, MemoryDeleteResult):
        return value.key
    if isinstance(value, LoadedSkill):
        return value.description
    if isinstance(value, TopicFindings):
        return ", ".join(
            found.name if found.error is None else f"{found.name} (failed)"
            for found in value.findings
        )
    return ""


class NodestepAgent:
    """The text-to-sql-demo agent: a nodestep graph behind the ``ChatAgent`` protocol.

    Every text-to-sql-demo thread is a nodestep thread with the same id; every call
    passes ``thread_id`` and the thread's database as ``context=``. Each
    text-to-sql-demo branch runs on a nodestep branch. ``AgentPositions`` keeps which
    one, the last completed event of each branch and the state before each
    user turn:

    - ``edit`` forks the thread at the state before the edited turn;
    - ``cancel``, and the next ``run`` after a failed one, fork the branch at
      its last completed event. Tool calls that were requested there but did
      not run get a tool message saying so, so the history stays valid.

    Parameters
    ----------
    graph
        A graph from ``build_graph``, with a state store; possibly instrumented.
    registry
        Where the thread's database is found.
    positions
        Where the branch positions and turn starts are kept.
    max_rows, timeout_ms
        The query limits, as in ``QueryExecutor``.
    model
        The model name ``usage`` frames report when the model names none.
    tracing
        The tracing the graph was instrumented with, shut down by ``close``.
    """

    def __init__(
        self,
        graph: Graph[AgentState],
        registry: DatabaseRegistry,
        positions: AgentPositions,
        *,
        max_rows: int,
        timeout_ms: int,
        model: str,
        tracing: Tracing | None = None,
    ) -> None:
        self._graph = graph
        self._registry = registry
        self._positions = positions
        self._max_rows = max_rows
        self._timeout_ms = timeout_ms
        self._model = model
        self._tracing = tracing

    @classmethod
    def from_settings(
        cls, chat: Chat, settings: ServerSettings, *, model: str, provider_name: str
    ) -> Self:
        """Build the agent over ``chat`` with its files in ``settings.data_dir``.

        The threads are kept in ``state.jsonl`` and the positions in
        ``agent.json``. The memory is in ``settings.memory_folder``; the
        skills are the shipped ones and those in ``settings.skills_folder``,
        read once, here. With ``settings.otlp_endpoint`` set, the graph and
        the sub-agent graph are traced with nodeartifact, and model spans
        name ``provider_name`` as their provider; call ``close`` when the
        server stops.

        Raises
        ------
        SkillFormatError
            If a ``SKILL.md`` has no valid frontmatter.
        SkillConflictError
            If two skills have the same name.
        """
        skills = FilesystemSkills(
            skill_roots(settings.skills_folder), nodes={MODEL_NODE}
        )
        analyst = build_analyst(chat, skills=skills)
        tracing = None
        if settings.otlp_endpoint is not None:
            tracing = Tracing(settings.otlp_endpoint, provider_name=provider_name)
            analyst = tracing.instrument(analyst)
        graph = build_graph(
            chat,
            state_store=FilesystemStateStore(settings.data_dir / "state.jsonl"),
            memory_tools=MemoryStore(settings.memory_folder).tools(),
            skills=skills,
            analyst=analyst,
        )
        if tracing is not None:
            graph = tracing.instrument(graph)
        return cls(
            graph,
            DatabaseRegistry(settings.databases_dir),
            AgentPositions(settings.data_dir / "agent.json"),
            max_rows=settings.max_rows,
            timeout_ms=settings.query_timeout_ms,
            model=model,
            tracing=tracing,
        )

    def close(self) -> None:
        """Send the traces not sent yet and stop tracing; without tracing, do nothing."""
        if self._tracing is not None:
            self._tracing.shutdown()

    async def run(
        self, *, thread_id: str, database: str, branch: str, turn_id: str, message: str
    ) -> AsyncGenerator[Frame, None]:
        """Answer ``message`` at the end of ``branch``; see ``ChatAgent.run``."""
        session = self._session(database)
        position = await self._ready(thread_id, branch)
        before = await self._latest_event(thread_id, position.nodestep_branch)
        self._positions.set_turn(
            thread_id,
            turn_id,
            TurnStart(nodestep_branch=position.nodestep_branch, before=before),
        )
        async with aclosing(
            self._stream(
                thread_id, branch, position, session, message=(turn_id, message)
            )
        ) as frames:
            async for frame in frames:
                yield frame

    async def resume(
        self,
        *,
        thread_id: str,
        database: str,
        branch: str,
        turn_id: str,
        key: str,
        answer: str,
    ) -> AsyncGenerator[Frame, None]:
        """Answer the clarification ``key`` with ``answer``; see ``ChatAgent.resume``.

        Raises
        ------
        BranchNotFoundError
            If this agent never ran ``branch``.
        ResumeError
            If the branch does not wait for ``key``.
        """
        session = self._session(database)
        position = self._positions.branch(thread_id, branch)
        if position is None:
            raise BranchNotFoundError(f"Thread {thread_id} has no branch {branch}.")
        async with aclosing(
            self._stream(
                thread_id,
                branch,
                position,
                session,
                resume=Resume(answers={key: answer}),
            )
        ) as frames:
            async for frame in frames:
                yield frame

    async def edit(
        self, *, thread_id: str, database: str, branch: str, turn_id: str, message: str
    ) -> AsyncGenerator[Frame, None]:
        """Answer ``message`` as a new version of ``turn_id``; see ``ChatAgent.edit``.

        The message gets a new id, so tool-call ids that a model builds from
        it differ between the versions.

        Raises
        ------
        TurnPositionNotFoundError
            If this agent did not answer ``turn_id``.
        """
        session = self._session(database)
        start = self._positions.turn(thread_id, turn_id)
        record = await self._graph.fork(
            thread_id,
            from_=start.before,
            branch_id=start.nodestep_branch,
            name=new_id(),
        )
        position = BranchPosition(
            nodestep_branch=record.id,
            last_completed=await self._latest_event(thread_id, record.id),
        )
        self._positions.set_branch(thread_id, branch, position)
        async with aclosing(
            self._stream(
                thread_id, branch, position, session, message=(new_id(), message)
            )
        ) as frames:
            async for frame in frames:
                yield frame

    async def cancel(self, *, thread_id: str, database: str, branch: str) -> None:
        """Continue ``branch`` from its last completed state; see ``ChatAgent.cancel``."""
        position = self._positions.branch(thread_id, branch)
        if position is not None and position.status != "settled":
            await self._settle(thread_id, branch, position)

    def _session(self, database: str) -> DatabaseSession:
        return DatabaseSession(
            self._registry.get(database),
            max_rows=self._max_rows,
            timeout_ms=self._timeout_ms,
        )

    async def _ready(self, thread_id: str, branch: str) -> BranchPosition:
        position = self._positions.branch(thread_id, branch)
        if position is not None:
            if position.status == "settled":
                return position
            return await self._settle(thread_id, branch, position)
        if branch != MAIN_BRANCH:
            raise BranchNotFoundError(f"Thread {thread_id} has no branch {branch}.")
        if await self._graph.exists(thread_id):
            event = await self._latest_event(thread_id, MAIN_BRANCH)
        else:
            event = await self._graph.update_state(thread_id, {}, create=True)
        position = BranchPosition(nodestep_branch=MAIN_BRANCH, last_completed=event)
        self._positions.set_branch(thread_id, branch, position)
        return position

    async def _settle(
        self, thread_id: str, branch: str, position: BranchPosition
    ) -> BranchPosition:
        record = await self._graph.fork(
            thread_id,
            from_=position.last_completed,
            branch_id=position.nodestep_branch,
            name=new_id(),
        )
        snapshot = await self._graph.get_state(thread_id, branch_id=record.id)
        pending = snapshot.value.tool_calls
        if pending:
            await self._graph.update_state(
                thread_id,
                {
                    "messages": [
                        ToolMessage(
                            content=STOPPED_CALL, name=call.name, tool_call_id=call.id
                        )
                        for call in pending
                    ],
                    "tool_calls": [],
                },
                branch_id=record.id,
            )
        settled = BranchPosition(
            nodestep_branch=record.id,
            last_completed=await self._latest_event(thread_id, record.id),
        )
        self._positions.set_branch(thread_id, branch, settled)
        return settled

    async def _latest_event(self, thread_id: str, nodestep_branch: str) -> str:
        history = await self._graph.history(thread_id, branch_id=nodestep_branch)
        return max(history.events, key=lambda event: event.sequence).id

    async def _stream(
        self,
        thread_id: str,
        branch: str,
        position: BranchPosition,
        session: DatabaseSession,
        *,
        message: tuple[str, str] | None = None,
        resume: Resume | None = None,
    ) -> AsyncGenerator[Frame, None]:
        translator = FrameTranslator(self._model)
        last_completed = position.last_completed
        status: BranchStatus = "unsettled"
        self._positions.set_branch(
            thread_id, branch, position.model_copy(update={"status": status})
        )
        initial = (
            None
            if message is None
            else {"messages": [HumanMessage(id=message[0], content=message[1])]}
        )
        events = self._graph.astream(
            initial,
            stream_mode=STREAM_MODES,
            thread_id=thread_id,
            branch_id=position.nodestep_branch,
            resume=resume,
            context=session,
        )
        try:
            async with aclosing(events):
                async for event in events:
                    if event.mode == "updates" and event.checkpoint_id is not None:
                        last_completed = event.checkpoint_id
                    elif event.mode == "final":
                        status = "settled"
                        last_completed = event.checkpoint_id or last_completed
                    elif event.mode == "interrupt":
                        status = "waiting"
                    for frame in translator.frames(event):
                        yield frame
        finally:
            self._positions.set_branch(
                thread_id,
                branch,
                BranchPosition(
                    nodestep_branch=position.nodestep_branch,
                    last_completed=last_completed,
                    status=status,
                ),
            )
