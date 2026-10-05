import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import suppress
from pathlib import Path

import pytest
from nodestep import ScriptedChat
from nodestep.chat import Chat, ChatRequest, ChatResponse, ToolMessage

from harness import BlockedChat, RoutingChat, calls, tool_call
from text_to_sql_demo import subagents
from text_to_sql_demo.frames import FinalFrame, Frame, SqlResultFrame, ToolFrame
from text_to_sql_demo.nodestep_agent import MAX_TOOL_CALLS, STOPPED_CALL, NodestepAgent
from text_to_sql_demo.settings import ServerSettings
from text_to_sql_demo.subagents import (
    ANALYST_PROMPT,
    ANALYST_STEPS,
    ANALYST_TOOL_CALLS,
    MAX_TOPIC_CALLS,
    MAX_TOPICS,
    TopicFindings,
)

TEACHERS = "How many teachers are there?"
COURSES = "How many courses are there?"
TOPICS = [
    {"name": "teachers", "question": TEACHERS},
    {"name": "courses", "question": COURSES},
]
WHERE = {"thread_id": "t1", "database": "fixture", "branch": "main"}


@pytest.fixture
def server_settings(
    database_path: Path, data_dir: Path, tmp_path: Path
) -> ServerSettings:
    return ServerSettings.model_validate(
        {
            "TEXT_TO_SQL_DEMO_DATA_DIR": str(data_dir),
            "TEXT_TO_SQL_DEMO_WEB_DIST": str(tmp_path / "no-dist"),
        }
    )


def make_agent(settings: ServerSettings, chat: Chat) -> NodestepAgent:
    return NodestepAgent.from_settings(
        chat, settings, model="gpt-6-luna", provider_name="openai"
    )


def analyst(sql: str, answer: str) -> ScriptedChat:
    return ScriptedChat([calls(tool_call("run_sql", {"sql": sql}, "a1")), answer])


def counting_analysts() -> dict[str, Chat]:
    return {
        TEACHERS: analyst("SELECT count(*) AS teachers FROM Teacher", "3 teachers."),
        COURSES: analyst("SELECT count(*) AS courses FROM Course", "3 courses."),
    }


def split(topics: object = TOPICS) -> ChatResponse:
    return calls(tool_call("analyze_topics", {"topics": topics}, "c1"))


async def collect(frames: AsyncGenerator[Frame, None]) -> list[Frame]:
    return [frame async for frame in frames]


async def ask(agent: NodestepAgent, message: str = "Report?") -> list[Frame]:
    return await collect(agent.run(**WHERE, turn_id="u1", message=message))


def only[F](frames: list[Frame], kind: type[F]) -> list[F]:
    return [frame for frame in frames if isinstance(frame, kind)]


def findings_sent(chat: ScriptedChat) -> TopicFindings:
    [message] = [
        message
        for message in chat.requests[-1].messages
        if isinstance(message, ToolMessage) and message.name == "analyze_topics"
    ]
    return TopicFindings.model_validate_json(message.content or "")


async def test_each_topic_gets_a_sub_agent_whose_answer_reaches_the_model(
    server_settings: ServerSettings,
):
    main = ScriptedChat([split(), "There are 3 teachers and 3 courses."])
    analysts = counting_analysts()
    frames = await ask(make_agent(server_settings, RoutingChat(main, analysts)))
    findings = findings_sent(main).findings
    assert [(found.name, found.answer, found.error) for found in findings] == [
        ("teachers", "3 teachers.", None),
        ("courses", "3 courses.", None),
    ]
    assert [
        [(query.row_count, "Teacher" in query.sql) for query in found.queries]
        for found in findings
    ] == [[(1, True)], [(1, False)]]
    tools = only(frames, ToolFrame)
    assert [(tool.name, tool.status) for tool in tools] == [
        ("analyze_topics", "started"),
        ("analyze_topics", "finished"),
    ]
    assert tools[0].arguments == {"topics": TOPICS}
    assert tools[1].summary == "teachers, courses"
    results = only(frames, SqlResultFrame)
    assert [result.call_id for result in results] == ["c1/1/1", "c1/2/1"]
    assert [result.columns for result in results] == [["teachers"], ["courses"]]
    assert [result.rows for result in results] == [[[3]], [[3]]]
    [final] = only(frames, FinalFrame)
    assert final.text == "There are 3 teachers and 3 courses."


async def test_the_model_gets_the_answers_and_queries_but_not_the_rows(
    server_settings: ServerSettings,
):
    main = ScriptedChat([split(), "Done."])
    await ask(make_agent(server_settings, RoutingChat(main, counting_analysts())))
    [message] = [
        m
        for m in main.requests[-1].messages
        if isinstance(m, ToolMessage) and m.name == "analyze_topics"
    ]
    sent = json.loads(message.content or "")
    assert set(sent["findings"][0]) == {
        "name",
        "question",
        "answer",
        "error",
        "queries",
    }
    assert set(sent["findings"][0]["queries"][0]) == {"sql", "row_count"}


async def test_a_sub_agent_has_the_sql_tools_and_the_skills_only(
    server_settings: ServerSettings,
):
    teachers = analyst("SELECT count(*) AS teachers FROM Teacher", "3 teachers.")
    analysts = {**counting_analysts(), TEACHERS: teachers}
    main = ScriptedChat([split(), "Done."])
    await ask(make_agent(server_settings, RoutingChat(main, analysts)))
    request: ChatRequest = teachers.requests[0]
    assert [tool.name for tool in request.tools] == [
        "list_tables",
        "describe_table",
        "run_sql",
        "load_skill",
    ]
    assert request.messages[0].content == ANALYST_PROMPT
    assert request.messages[-1].content == TEACHERS
    skills = [m.content or "" for m in request.messages if m.type == "system"]
    assert any("- report:" in text for text in skills)


async def test_a_failed_sub_agent_is_named_and_the_others_still_answer(
    server_settings: ServerSettings,
):
    analysts: dict[str, Chat] = {
        TEACHERS: analyst("SELECT count(*) AS teachers FROM Teacher", "3 teachers."),
        COURSES: ScriptedChat([]),
    }
    main = ScriptedChat([split(), "Only the teachers are known."])
    frames = await ask(make_agent(server_settings, RoutingChat(main, analysts)))
    teachers, courses = findings_sent(main).findings
    assert teachers.answer == "3 teachers."
    assert courses.answer is None
    assert courses.error is not None
    assert "no response left" in courses.error
    assert only(frames, ToolFrame)[-1].summary == "teachers, courses (failed)"


@pytest.mark.parametrize(
    "topics",
    [
        TOPICS[:1],
        [*TOPICS, *TOPICS, TOPICS[0]][: MAX_TOPICS + 1],
        [{"name": "", "question": TEACHERS}, TOPICS[1]],
    ],
    ids=["one-topic", "too-many", "blank-name"],
)
async def test_topics_that_do_not_validate_go_back_to_the_model(
    server_settings: ServerSettings, topics: list[dict]
):
    main = ScriptedChat([split(topics), "Sorry."])
    frames = await ask(make_agent(server_settings, RoutingChat(main, {})))
    failed = only(frames, ToolFrame)[-1]
    assert (failed.name, failed.status) == ("analyze_topics", "error")
    [message] = [m for m in main.requests[-1].messages if isinstance(m, ToolMessage)]
    assert (message.content or "").startswith("Tool error: ValidationError: ")


async def test_analyze_topics_with_other_tools_in_the_step_is_refused(
    server_settings: ServerSettings,
):
    main = ScriptedChat(
        [
            calls(
                tool_call("list_tables", {}, "c0"),
                tool_call("analyze_topics", {"topics": TOPICS}, "c1"),
            ),
            "Done.",
        ]
    )
    frames = await ask(make_agent(server_settings, RoutingChat(main, {})))
    statuses = {
        tool.name: tool.status
        for tool in only(frames, ToolFrame)
        if tool.status != "started"
    }
    assert statuses == {"list_tables": "finished", "analyze_topics": "error"}
    refused = next(tool for tool in only(frames, ToolFrame) if tool.status == "error")
    assert refused.summary is not None
    assert refused.summary.startswith("Call analyze_topics on its own")


async def test_stopping_the_answer_stops_the_sub_agents(
    server_settings: ServerSettings,
):
    blocked = {TEACHERS: BlockedChat(), COURSES: BlockedChat()}
    main = ScriptedChat([split(), "Recovered."])
    agent = make_agent(server_settings, RoutingChat(main, blocked))
    seen: list[Frame] = []

    async def consume() -> None:
        async for frame in agent.run(**WHERE, turn_id="u1", message="Report?"):
            seen.append(frame)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(
        asyncio.gather(*(chat.started.wait() for chat in blocked.values())), 5
    )
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert all(chat.cancelled.is_set() for chat in blocked.values())
    assert [tool.status for tool in only(seen, ToolFrame)] == ["started"]
    await agent.cancel(**WHERE)
    frames = await collect(agent.run(**WHERE, turn_id="u3", message="Again?"))
    assert only(frames, FinalFrame)[0].text == "Recovered."
    stopped = [
        m
        for m in main.requests[-1].messages
        if isinstance(m, ToolMessage) and m.tool_call_id == "c1"
    ]
    assert [m.content for m in stopped] == [STOPPED_CALL]


async def test_sub_agents_that_take_too_long_are_stopped_and_named(
    server_settings: ServerSettings, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(subagents, "ANALYST_TIMEOUT_SECONDS", 0.2)
    slow = BlockedChat()
    analysts: dict[str, Chat] = {
        TEACHERS: analyst("SELECT count(*) AS teachers FROM Teacher", "3 teachers."),
        COURSES: slow,
    }
    main = ScriptedChat([split(), "Only the teachers are known."])
    await ask(make_agent(server_settings, RoutingChat(main, analysts)))
    teachers, courses = findings_sent(main).findings
    assert teachers.answer == "3 teachers."
    assert courses.error == "It did not finish within 0.2 seconds."
    assert slow.cancelled.is_set()


def answering_analysts(count: int) -> dict[str, Chat]:
    return {
        TEACHERS: ScriptedChat(["3 teachers."] * count),
        COURSES: ScriptedChat(["3 courses."] * count),
    }


def tool_results(chat: ScriptedChat, name: str) -> list[str]:
    return [
        m.content or ""
        for m in chat.requests[-1].messages
        if isinstance(m, ToolMessage) and m.name == name
    ]


async def test_an_answer_can_call_analyze_topics_only_so_often(
    server_settings: ServerSettings,
):
    splits = [
        calls(tool_call("analyze_topics", {"topics": TOPICS}, f"c{number}"))
        for number in range(1, MAX_TOPIC_CALLS + 2)
    ]
    main = ScriptedChat([*splits, "Done."])
    analysts = answering_analysts(MAX_TOPIC_CALLS)
    frames = await ask(make_agent(server_settings, RoutingChat(main, analysts)))
    results = tool_results(main, "analyze_topics")
    assert len(results) == MAX_TOPIC_CALLS + 1
    assert all(result.startswith('{"findings"') for result in results[:-1])
    assert results[-1] == (
        f"Tool 'analyze_topics' denied: per-tool limit exceeded ({MAX_TOPIC_CALLS})"
    )
    assert only(frames, ToolFrame)[-1].status == "error"


async def test_analyze_topics_counts_toward_the_tool_budget_of_the_answer(
    server_settings: ServerSettings,
):
    listings = [
        calls(tool_call("list_tables", {}, f"c{number}"))
        for number in range(2, MAX_TOOL_CALLS + 2)
    ]
    main = ScriptedChat([split(), *listings, "Done."])
    frames = await ask(
        make_agent(server_settings, RoutingChat(main, answering_analysts(1)))
    )
    results = tool_results(main, "list_tables")
    assert len(results) == MAX_TOOL_CALLS
    assert results[-1] == (
        f"Tool 'list_tables' denied: total tool limit exceeded ({MAX_TOOL_CALLS})"
    )
    assert only(frames, FinalFrame)[0].text == "Done."


def listing_calls(count: int) -> list[ChatResponse]:
    return [
        calls(tool_call("list_tables", {}, f"a{number}"))
        for number in range(1, count + 1)
    ]


async def test_a_sub_agent_is_denied_tool_calls_over_its_budget(
    server_settings: ServerSettings,
):
    busy = ScriptedChat([*listing_calls(ANALYST_TOOL_CALLS + 1), "Gave up."])
    analysts = {**counting_analysts(), TEACHERS: busy}
    main = ScriptedChat([split(), "Done."])
    await ask(make_agent(server_settings, RoutingChat(main, analysts)))
    denied = tool_results(busy, "list_tables")
    assert len(denied) == ANALYST_TOOL_CALLS + 1
    assert denied[-1] == (
        f"Tool 'list_tables' denied: total tool limit exceeded ({ANALYST_TOOL_CALLS})"
    )
    teachers, courses = findings_sent(main).findings
    assert (teachers.answer, courses.answer) == ("Gave up.", "3 courses.")


async def test_a_sub_agent_that_keeps_calling_tools_ends_as_a_failed_topic(
    server_settings: ServerSettings,
):
    endless = ScriptedChat(listing_calls(ANALYST_STEPS))
    analysts = {**counting_analysts(), TEACHERS: endless}
    main = ScriptedChat([split(), "Done."])
    await ask(make_agent(server_settings, RoutingChat(main, analysts)))
    teachers, courses = findings_sent(main).findings
    assert teachers.answer is None
    assert teachers.error is not None
    assert str(ANALYST_STEPS) in teachers.error
    assert courses.answer == "3 courses."
    assert len(endless.requests) <= ANALYST_STEPS // 2 + 1
