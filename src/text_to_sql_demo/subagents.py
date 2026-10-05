from typing import Any

from nodestep import (
    AgentResult,
    AgentState,
    AgentTask,
    AgentTimeoutError,
    Graph,
    Node,
    NodeContext,
    ToolContext,
    ToolDeniedError,
    build_react_agent,
    call_tool,
    node,
    tool,
)
from nodestep.chat import Chat, HumanMessage, ToolCall, ToolMessage
from nodestep.middleware import FilesystemSkills
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from text_to_sql_demo.errors import SessionContextError, SubAgentsAloneError
from text_to_sql_demo.execution import SqlResult
from text_to_sql_demo.tools import DatabaseSession, describe_table, list_tables, run_sql

MAX_TOPICS = 4
MAX_TOPIC_CALLS = 2
DELEGATE_NODE = "delegate"
ANALYST_TOOL_CALLS = 10
ANALYST_STEPS = 2 * (ANALYST_TOOL_CALLS + 1) + 1
ANALYST_TIMEOUT_SECONDS = 300.0
ANALYST_PROMPT = """\
You answer one part of a larger question about a SQLite database. Another \
agent writes the final answer from your answer.

- Use list_tables and describe_table as needed, and base every number on \
run_sql results. Write one read-only SELECT in SQLite syntax, with text values \
in single quotes. When a query is rejected or fails, fix it and run it again.
- Answer in two or three plain sentences with the key numbers and what you \
counted.
"""


class Topic(BaseModel):
    """One part of a question, for one sub-agent."""

    name: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=1000)


class TopicRequest(BaseModel):
    """The arguments of ``analyze_topics``."""

    model_config = ConfigDict(extra="forbid")

    topics: list[Topic] = Field(min_length=2, max_length=MAX_TOPICS)


class TopicQuery(BaseModel):
    """A query a sub-agent ran: the SQL and how many rows it returned."""

    sql: str
    row_count: int


class TopicFinding(BaseModel):
    """What the sub-agent of one topic found.

    ``answer`` is its final text and ``queries`` its successful queries,
    without their rows. ``error`` says why there is no answer.
    """

    name: str
    question: str
    answer: str | None
    error: str | None
    queries: list[TopicQuery]


class TopicFindings(BaseModel):
    """The result of ``analyze_topics``: one finding per topic, in order."""

    findings: list[TopicFinding]


class TopicResults(BaseModel):
    """The query results of the sub-agents of one ``analyze_topics`` call.

    ``delegate`` sends it as a ``custom`` stream event, so the answer shows
    the rows that the model does not get. ``results`` holds one list per
    topic, in order.
    """

    call_id: str
    results: list[list[SqlResult]]


@tool(input_model=TopicRequest)
def analyze_topics(ctx: ToolContext, topics: list[Topic]) -> TopicRequest:
    """Answer a question with separate parts, such as a report, with one sub-agent per part.

    Each topic goes to its own sub-agent, which has list_tables,
    describe_table and run_sql on this database. They run at the same time
    and each returns a short answer and the queries it ran. Call it on its
    own, without other tools in the same step, at most twice per answer.

    Parameters
    ----------
    topics
        2 to 4 parts. Each has a short name, such as "returns", and a
        question that names the period and filters it needs.
    """
    if ctx.node != DELEGATE_NODE:
        raise SubAgentsAloneError(
            "Call analyze_topics on its own, without other tools in the same "
            "step. The other calls of this step ran normally."
        )
    return TopicRequest(topics=topics)


def build_analyst(chat: Chat, *, skills: FilesystemSkills) -> Graph[AgentState]:
    """Build the graph of one sub-agent.

    Parameters
    ----------
    chat
        The model, the same one the main agent uses.
    skills
        The skills, so a sub-agent can load one too.

    Returns
    -------
    Graph[AgentState]
        A ReAct agent named ``text-to-sql-demo-analyst`` with ``list_tables``,
        ``describe_table`` and ``run_sql``, within ``ANALYST_TOOL_CALLS``
        tool calls. It keeps no state: each sub-agent runs once on its own
        thread.
    """
    return build_react_agent(
        chat,
        tools=[list_tables, describe_table, run_sql],
        name="text-to-sql-demo-analyst",
        system_prompt=ANALYST_PROMPT,
        middleware=[skills],
        tool_errors="return",
        stream=True,
        max_steps=ANALYST_STEPS,
        max_tool_calls=ANALYST_TOOL_CALLS,
    )


def delegate_node(analyst: Graph[AgentState]) -> Node:
    """Return the node that answers an ``analyze_topics`` call.

    The graph routes a step here when its only tool call is
    ``analyze_topics``. The node runs the call through ``call_tool``, so the
    graph's tool middleware sees it: it counts toward the answer's tool
    budget, and over ``MAX_TOPIC_CALLS`` calls it is denied. Then it starts
    one sub-agent per topic with ``ctx.spawn``, each on the run's
    ``DatabaseSession`` and its own thread, and waits for them with
    ``ctx.gather``, at most ``ANALYST_TIMEOUT_SECONDS``. A stopped run
    cancels them. Arguments that do not validate go back to the model as a
    tool error.

    Parameters
    ----------
    analyst
        The sub-agent graph from ``build_analyst``; possibly instrumented.

    Returns
    -------
    Node
        The node ``delegate``.
    """

    @node(name=DELEGATE_NODE)
    async def delegate(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
        messages = [
            await run_topics(call, analyst, state, ctx) for call in state.tool_calls
        ]
        return {"messages": messages, "tool_calls": []}

    return delegate


async def run_topics(
    call: ToolCall, analyst: Graph[AgentState], state: AgentState, ctx: NodeContext
) -> ToolMessage:
    """Run the sub-agents of one ``analyze_topics`` call and return its tool message.

    The message holds the ``TopicFindings``. The query results go out as a
    ``TopicResults`` stream event.
    """
    try:
        called = await call_tool(
            analyze_topics,
            call.arguments,
            ToolContext.from_node_context(ctx, state, tool_call_id=call.id),
        )
    except ToolDeniedError as error:
        return ToolMessage(content=str(error), name=call.name, tool_call_id=call.id)
    except ValidationError as error:
        return ToolMessage(
            content=f"Tool error: ValidationError: {error}",
            name=call.name,
            tool_call_id=call.id,
        )
    request = TopicRequest.model_validate(called.value)
    session = ctx.context
    if not isinstance(session, DatabaseSession):
        raise SessionContextError(
            f"analyze_topics needs a DatabaseSession as context=, got {type(session).__name__}"
        )
    handles = await ctx.spawn(
        [
            AgentTask(
                graph=analyst,
                input={
                    "messages": [
                        HumanMessage(id=f"{call.id}-{number}", content=topic.question)
                    ]
                },
                name=topic.name,
                context=session,
            )
            for number, topic in enumerate(request.topics, start=1)
        ]
    )
    try:
        results: list[AgentResult | None] = list(
            await ctx.gather(
                handles, timeout=ANALYST_TIMEOUT_SECONDS, return_exceptions=True
            )
        )
    except AgentTimeoutError as error:
        finished = {result.id: result for result in error.completed}
        results = [finished.get(handle.id) for handle in handles]
    queries = [query_results(result) for result in results]
    ctx.emit(TopicResults(call_id=call.id, results=queries))
    findings = TopicFindings(
        findings=[
            finding(topic, result, found)
            for topic, result, found in zip(
                request.topics, results, queries, strict=True
            )
        ]
    )
    return ToolMessage(
        content=findings.model_dump_json(), name=call.name, tool_call_id=call.id
    )


def finding(
    topic: Topic, result: AgentResult | None, results: list[SqlResult]
) -> TopicFinding:
    """Return what the sub-agent of ``topic`` found; ``None`` means it timed out."""
    if result is None:
        return TopicFinding(
            name=topic.name,
            question=topic.question,
            answer=None,
            error=f"It did not finish within {ANALYST_TIMEOUT_SECONDS:g} seconds.",
            queries=[],
        )
    state = result.state
    if result.error is not None or not isinstance(state, AgentState):
        reason = result.error or result.status
        return TopicFinding(
            name=topic.name,
            question=topic.question,
            answer=None,
            error=str(reason) or type(reason).__name__,
            queries=[],
        )
    return TopicFinding(
        name=topic.name,
        question=topic.question,
        answer=state.final_text,
        error=None,
        queries=[
            TopicQuery(sql=found.sql, row_count=found.row_count) for found in results
        ],
    )


def query_results(result: AgentResult | None) -> list[SqlResult]:
    """Return the successful ``run_sql`` results of a sub-agent that finished, in order."""
    if result is None or not isinstance(result.state, AgentState):
        return []
    found: list[SqlResult] = []
    for message in result.state.messages:
        if isinstance(message, ToolMessage) and message.name == run_sql.name:
            try:
                found.append(SqlResult.model_validate_json(message.content or ""))
            except ValidationError:
                continue
    return found
