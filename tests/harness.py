import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence

from nodestep import ScriptedChat, Tool, build_react_agent
from nodestep.chat import (
    AIMessage,
    Chat,
    ChatRequest,
    ChatResponse,
    ChatStreamChunk,
    HumanMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolMessage,
)

from text_to_sql_demo.execution import SqlResult
from text_to_sql_demo.subagents import ANALYST_PROMPT
from text_to_sql_demo.tools import DatabaseSession


def tool_call(
    name: str, arguments: Mapping[str, object], call_id: str = "c1"
) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=dict(arguments))


def calls(*items: ToolCall) -> ChatResponse:
    return ChatResponse(tool_calls=list(items))


def earlier_result(result: SqlResult, call_id: str = "c0") -> list[Message]:
    return [
        HumanMessage(content="earlier question"),
        AIMessage(tool_calls=[tool_call("run_sql", {"sql": result.sql}, call_id)]),
        ToolMessage(
            content=result.model_dump_json(), name="run_sql", tool_call_id=call_id
        ),
        AIMessage(content="earlier answer"),
    ]


async def call_tools(
    tools: Sequence[Tool],
    first: ChatResponse,
    session: DatabaseSession | None,
    *,
    before: Sequence[Message] = (),
) -> list[ToolMessage]:
    graph = build_react_agent(
        ScriptedChat([first, "done"]), tools=tools, tool_errors="raise"
    )
    result = await graph.ainvoke(
        {"messages": [*before, HumanMessage(content="question")]}, context=session
    )
    answered = {call.id for call in first.tool_calls}
    return [
        message
        for message in result.state.messages
        if isinstance(message, ToolMessage) and message.tool_call_id in answered
    ]


async def call_tool_once(
    tool: Tool,
    arguments: Mapping[str, object],
    session: DatabaseSession | None,
    *,
    before: Sequence[Message] = (),
) -> ToolMessage:
    [message] = await call_tools(
        [tool], calls(tool_call(tool.name, arguments)), session, before=before
    )
    return message


class HoldingChat(ScriptedChat):
    """A scripted chat whose request number ``hold`` waits until it is cancelled."""

    def __init__(self, responses: list[ChatResponse | str], *, hold: int) -> None:
        super().__init__(responses)
        self.hold = hold

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if len(self.requests) + 1 == self.hold:
            self.requests.append(request)
            await asyncio.Event().wait()
        return await super().complete(request)


def is_analyst(request: ChatRequest) -> bool:
    return any(
        isinstance(message, SystemMessage) and message.content == ANALYST_PROMPT
        for message in request.messages
    )


def first_question(request: ChatRequest) -> str:
    return next(
        message.content or ""
        for message in request.messages
        if isinstance(message, HumanMessage)
    )


class RoutingChat:
    """Answers the main agent with ``main`` and each sub-agent with the chat for its question."""

    model = "scripted"

    def __init__(self, main: Chat, analysts: Mapping[str, Chat]) -> None:
        self.main = main
        self.analysts = dict(analysts)

    def pick(self, request: ChatRequest) -> Chat:
        if is_analyst(request):
            return self.analysts[first_question(request)]
        return self.main

    async def complete(self, request: ChatRequest) -> ChatResponse:
        return await self.pick(request).complete(request)

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        return self.pick(request).stream(request)


class BlockedChat(ScriptedChat):
    """A scripted chat whose every request waits until it is cancelled, and says so."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ChatResponse()

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        yield ChatStreamChunk(content_delta=(await self.complete(request)).content)
