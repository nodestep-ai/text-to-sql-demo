import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from pathlib import Path

import pytest
from nodestep import GraphConfigError, ResumeError, ScriptedChat
from nodestep.chat import (
    AIMessage,
    Chat,
    ChatRequest,
    ChatResponse,
    HumanMessage,
    ToolMessage,
)

from harness import HoldingChat, calls, tool_call
from text_to_sql_demo.agent import ChatAgent
from text_to_sql_demo.demo import DemoChat
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
from text_to_sql_demo.nodestep_agent import MAX_TOOL_CALLS, STOPPED_CALL, NodestepAgent
from text_to_sql_demo.positions import AgentPositions
from text_to_sql_demo.schema import DatabaseSchema
from text_to_sql_demo.settings import ServerSettings
from text_to_sql_demo.tools import ClarificationAnswer, DatabaseSession

WHERE = {"thread_id": "t1", "database": "fixture"}
PROPOSALS = [
    {"id": "all", "label": "All courses", "description": "Counts every course."},
    {"id": "sql", "label": "SQL courses", "description": "Counts SQL courses."},
]
COURSES_SQL = "SELECT count(*) AS courses FROM Course"


@pytest.fixture
def server_settings(
    database_path: Path, data_dir: Path, tmp_path: Path
) -> ServerSettings:
    return ServerSettings.model_validate(
        {
            "TEXT_TO_SQL_DEMO_DATA_DIR": str(data_dir),
            "TEXT_TO_SQL_DEMO_WEB_DIST": str(tmp_path / "no-dist"),
            "TEXT_TO_SQL_DEMO_MAX_ROWS": 10,
            "TEXT_TO_SQL_DEMO_QUERY_TIMEOUT_MS": 1000,
        }
    )


def make_agent(settings: ServerSettings, chat: Chat) -> NodestepAgent:
    return NodestepAgent.from_settings(
        chat, settings, model="gpt-6-luna", provider_name="openai"
    )


async def collect(frames: AsyncGenerator[Frame, None]) -> list[Frame]:
    return [frame async for frame in frames]


async def ask(
    agent: NodestepAgent, turn_id: str, message: str, branch: str = "main"
) -> list[Frame]:
    return await collect(
        agent.run(**WHERE, branch=branch, turn_id=turn_id, message=message)
    )


async def stop_when(
    frames: AsyncGenerator[Frame, None], condition: Callable[[Frame], bool]
) -> list[Frame]:
    seen: list[Frame] = []
    reached = asyncio.Event()

    async def consume() -> None:
        async for frame in frames:
            seen.append(frame)
            if condition(frame):
                reached.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(reached.wait(), 5)
    await asyncio.sleep(0.05)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    return seen


def only[F](frames: list[Frame], kind: type[F]) -> list[F]:
    return [frame for frame in frames if isinstance(frame, kind)]


def conversation(request: ChatRequest) -> list[tuple[str, str | None]]:
    return [
        (message.type, message.content)
        for message in request.messages
        if not isinstance(message, AIMessage) or message.content is not None
        if message.type != "system"
    ]


def test_the_agent_satisfies_the_protocol(server_settings: ServerSettings):
    agent: ChatAgent = make_agent(server_settings, ScriptedChat())
    assert agent is not None


async def test_a_run_streams_tools_result_chart_text_and_suggestions(
    server_settings: ServerSettings, schema: DatabaseSchema
):
    sql = "select TeacherId as teacher, count(*) as courses from Course group by TeacherId"
    chat = ScriptedChat(
        [
            calls(tool_call("list_tables", {}, "c1")),
            calls(tool_call("run_sql", {"sql": sql}, "c2")),
            calls(
                tool_call(
                    "make_chart",
                    {"kind": "bar", "title": "Courses", "x": "teacher", "y": "courses"},
                    "c3",
                ),
                tool_call("suggest_follow_ups", {"questions": ["A?", "B?"]}, "c4"),
            ),
            "Teacher 1 has two courses.",
        ]
    )
    frames = await ask(make_agent(server_settings, chat), "u1", "Courses per teacher?")
    kinds = [frame.type for frame in frames]
    assert kinds[:9] == [
        "tool",
        "tool",
        "tool",
        "tool",
        "sql_result",
        "tool",
        "tool",
        "chart",
        "token",
    ]
    assert kinds[-2:] == ["final", "suggestions"]
    tools = only(frames, ToolFrame)
    assert [(t.call_id, t.name, t.status) for t in tools] == [
        ("c1", "list_tables", "started"),
        ("c1", "list_tables", "finished"),
        ("c2", "run_sql", "started"),
        ("c2", "run_sql", "finished"),
        ("c3", "make_chart", "started"),
        ("c3", "make_chart", "finished"),
    ]
    assert tools[0].arguments == {}
    assert tools[1].summary == "3 tables: Course, Teacher, Video"
    assert tools[2].arguments == {"sql": sql}
    assert tools[3].summary == "2 rows"
    assert tools[5].summary == "bar chart"
    [result] = only(frames, SqlResultFrame)
    assert result.call_id == "c2"
    assert result.sql.startswith("SELECT\n")
    assert result.rows == [[1, 2], [2, 1]]
    [chart] = only(frames, ChartFrame)
    assert chart.call_id == "c3"
    assert chart.data == [{"teacher": 1, "courses": 2}, {"teacher": 2, "courses": 1}]
    [final] = only(frames, FinalFrame)
    assert final.text == "Teacher 1 has two courses."
    assert "".join(t.text for t in only(frames, TokenFrame)) == final.text
    assert only(frames, SuggestionsFrame)[0].questions == ["A?", "B?"]
    assert not only(frames, UsageFrame)
    assert [tool.name for tool in chat.requests[0].tools] == [
        "list_tables",
        "describe_table",
        "run_sql",
        "make_chart",
        "ask_user",
        "suggest_follow_ups",
        "analyze_topics",
        "save_memory",
        "search_memory",
        "list_memories",
        "delete_memory",
        "load_skill",
    ]
    assert chat.requests[0].messages[0].type == "system"


async def test_no_suggestions_frame_when_fewer_than_two_new_questions_are_left(
    server_settings: ServerSettings,
):
    chat = ScriptedChat(
        [
            calls(
                tool_call(
                    "suggest_follow_ups",
                    {"questions": ["Courses per teacher", "Longest video?"]},
                    "c1",
                )
            ),
            "Three courses.",
        ]
    )
    frames = await ask(make_agent(server_settings, chat), "u1", "Courses per teacher?")
    assert [frame.type for frame in frames][-1] == "final"
    assert not only(frames, SuggestionsFrame)


async def test_a_model_call_with_usage_sends_a_usage_frame(
    server_settings: ServerSettings,
):
    chat = ScriptedChat(
        [
            ChatResponse(
                content="Hello.",
                usage={"prompt_tokens": 12, "completion_tokens": 5},
                model="gpt-6-luna-2026-09-01",
            )
        ]
    )
    frames = await ask(make_agent(server_settings, chat), "u1", "Hi")
    [usage] = only(frames, UsageFrame)
    assert usage == UsageFrame(
        model="gpt-6-luna-2026-09-01", input_tokens=12, output_tokens=5
    )
    assert [frame.type for frame in frames] == ["token", "usage", "final"]


async def test_an_sql_error_reaches_the_model_which_corrects_the_query(
    server_settings: ServerSettings,
):
    chat = ScriptedChat(
        [
            calls(tool_call("run_sql", {"sql": "SELECT Nope FROM Course"}, "c1")),
            calls(tool_call("run_sql", {"sql": COURSES_SQL}, "c2")),
            "There are three courses.",
        ]
    )
    frames = await ask(make_agent(server_settings, chat), "u1", "How many courses?")
    tools = only(frames, ToolFrame)
    assert [(t.call_id, t.status) for t in tools] == [
        ("c1", "started"),
        ("c1", "error"),
        ("c2", "started"),
        ("c2", "finished"),
    ]
    assert tools[1].summary is not None
    assert tools[1].summary.startswith("Column 'nope' could not be resolved.")
    [result] = only(frames, SqlResultFrame)
    assert (result.call_id, result.rows) == ("c2", [[3]])
    sent = chat.requests[1].messages[-1]
    assert isinstance(sent, ToolMessage)
    assert (sent.content or "").startswith("Tool error: UnknownColumnError: Column")


async def test_calls_over_the_budget_are_denied(server_settings: ServerSettings):
    many = [tool_call("list_tables", {}, f"c{n}") for n in range(MAX_TOOL_CALLS + 1)]
    chat = ScriptedChat([calls(*many), "Done."])
    frames = await ask(make_agent(server_settings, chat), "u1", "Tables?")
    errors = [t for t in only(frames, ToolFrame) if t.status == "error"]
    assert len(errors) == 1
    assert "limit" in (errors[0].summary or "")
    assert only(frames, FinalFrame)[0].text == "Done."


async def test_a_configuration_error_raises(
    server_settings: ServerSettings, monkeypatch: pytest.MonkeyPatch
):
    def broken(self: DatabaseSession) -> DatabaseSchema:
        raise GraphConfigError("broken setup")

    monkeypatch.setattr(DatabaseSession, "schema", broken)
    chat = ScriptedChat([calls(tool_call("list_tables", {}, "c1")), "Done."])
    with pytest.raises(GraphConfigError, match="broken setup"):
        await ask(make_agent(server_settings, chat), "u1", "Tables?")


async def test_a_clarification_pauses_and_the_answer_resumes(
    server_settings: ServerSettings,
):
    chat = ScriptedChat(
        [
            calls(tool_call("list_tables", {}, "c1")),
            calls(
                tool_call(
                    "ask_user",
                    {"question": "Which courses?", "proposals": PROPOSALS},
                    "c2",
                )
            ),
            calls(tool_call("run_sql", {"sql": COURSES_SQL}, "c3")),
            "Three courses.",
        ]
    )
    agent = make_agent(server_settings, chat)
    asked = await ask(agent, "u1", "How many courses?")
    assert [frame.type for frame in asked] == ["tool", "tool", "clarification"]
    [question] = only(asked, ClarificationFrame)
    assert question.key.endswith(":clarify")
    assert question.question == "Which courses?"
    assert [proposal.id for proposal in question.proposals] == ["all", "sql"]
    assert question.allow_free_text is True
    answered = await collect(
        agent.resume(
            **WHERE, branch="main", turn_id="u2", key=question.key, answer="all"
        )
    )
    assert only(answered, FinalFrame)[0].text == "Three courses."
    assert only(answered, SqlResultFrame)[0].rows == [[3]]
    asked_ids = {tool.call_id for tool in only(asked, ToolFrame)}
    answered_ids = {tool.call_id for tool in only(answered, ToolFrame)}
    assert asked_ids == {"c1"}
    assert answered_ids == {"c3"}
    reply = chat.requests[2].messages[-1]
    assert isinstance(reply, ToolMessage)
    answer = ClarificationAnswer.model_validate_json(reply.content or "")
    assert answer.proposal is not None
    assert answer.proposal.label == "All courses"


async def test_an_edit_forks_at_the_state_before_the_turn(
    server_settings: ServerSettings,
):
    chat = ScriptedChat(["A1", "A2", "A1 again", "A3", "A4", "A2 again"])
    agent = make_agent(server_settings, chat)
    await ask(agent, "u1", "first")
    await ask(agent, "u2", "second")
    edited = await collect(
        agent.edit(**WHERE, branch="b1", turn_id="u1", message="first, edited")
    )
    assert only(edited, FinalFrame)[0].text == "A1 again"
    assert conversation(chat.requests[2]) == [("human", "first, edited")]
    await ask(agent, "u3", "third", branch="b1")
    assert conversation(chat.requests[3]) == [
        ("human", "first, edited"),
        ("ai", "A1 again"),
        ("human", "third"),
    ]
    await ask(agent, "u4", "fourth")
    assert conversation(chat.requests[4]) == [
        ("human", "first"),
        ("ai", "A1"),
        ("human", "second"),
        ("ai", "A2"),
        ("human", "fourth"),
    ]
    await collect(
        agent.edit(**WHERE, branch="b2", turn_id="u2", message="second, edited")
    )
    assert conversation(chat.requests[5]) == [
        ("human", "first"),
        ("ai", "A1"),
        ("human", "second, edited"),
    ]


async def test_stop_during_a_tool_continues_from_the_last_completed_state(
    server_settings: ServerSettings, monkeypatch: pytest.MonkeyPatch
):
    original = DatabaseSession.schema

    def slow(self: DatabaseSession) -> DatabaseSchema:
        time.sleep(0.3)
        return original(self)

    monkeypatch.setattr(DatabaseSession, "schema", slow)
    chat = ScriptedChat([calls(tool_call("list_tables", {}, "c1")), "After the stop."])
    agent = make_agent(server_settings, chat)
    seen = await stop_when(
        agent.run(**WHERE, branch="main", turn_id="u1", message="Tables?"),
        lambda frame: isinstance(frame, ToolFrame) and frame.status == "started",
    )
    assert [frame.type for frame in seen] == ["tool"]
    await agent.cancel(**WHERE, branch="main")
    frames = await ask(agent, "u2", "Again?")
    assert only(frames, FinalFrame)[0].text == "After the stop."
    messages = chat.requests[1].messages
    stopped = [m for m in messages if isinstance(m, ToolMessage)]
    assert [(m.tool_call_id, m.content) for m in stopped] == [("c1", STOPPED_CALL)]
    assert [m.content for m in messages if isinstance(m, HumanMessage)] == [
        "Tables?",
        "Again?",
    ]


async def test_stop_during_the_model_keeps_the_finished_tools(
    server_settings: ServerSettings,
):
    chat = HoldingChat(
        [calls(tool_call("list_tables", {}, "c1")), "Never sent.", "After the stop."],
        hold=2,
    )
    agent = make_agent(server_settings, chat)
    seen = await stop_when(
        agent.run(**WHERE, branch="main", turn_id="u1", message="Tables?"),
        lambda frame: isinstance(frame, ToolFrame) and frame.status == "finished",
    )
    assert [frame.type for frame in seen] == ["tool", "tool"]
    await agent.cancel(**WHERE, branch="main")
    frames = await ask(agent, "u2", "Again?")
    assert only(frames, FinalFrame)[0].text == "Never sent."
    messages = chat.requests[2].messages
    [result] = [m for m in messages if isinstance(m, ToolMessage)]
    assert result.tool_call_id == "c1"
    assert result.content != STOPPED_CALL


async def test_stop_during_a_clarification_drops_the_question(
    server_settings: ServerSettings,
):
    chat = ScriptedChat(
        [
            calls(
                tool_call("ask_user", {"question": "Which?", "proposals": PROPOSALS})
            ),
            "Something else.",
        ]
    )
    agent = make_agent(server_settings, chat)
    [question] = only(await ask(agent, "u1", "Courses?"), ClarificationFrame)
    await agent.cancel(**WHERE, branch="main")
    with pytest.raises(ResumeError):
        await collect(
            agent.resume(
                **WHERE, branch="main", turn_id="u2", key=question.key, answer="all"
            )
        )
    frames = await ask(agent, "u3", "Something else?")
    assert only(frames, FinalFrame)[0].text == "Something else."
    messages = chat.requests[1].messages
    assert [m.content for m in messages if isinstance(m, ToolMessage)] == [STOPPED_CALL]


async def test_a_failed_run_is_settled_before_the_next_message(
    server_settings: ServerSettings, monkeypatch: pytest.MonkeyPatch
):
    original = DatabaseSession.schema
    failures = [GraphConfigError("broken once")]

    def flaky(self: DatabaseSession) -> DatabaseSchema:
        if failures:
            raise failures.pop()
        return original(self)

    monkeypatch.setattr(DatabaseSession, "schema", flaky)
    chat = ScriptedChat([calls(tool_call("list_tables", {}, "c1")), "Works now."])
    agent = make_agent(server_settings, chat)
    with pytest.raises(GraphConfigError):
        await ask(agent, "u1", "Tables?")
    frames = await ask(agent, "u2", "Tables now?")
    assert only(frames, FinalFrame)[0].text == "Works now."
    messages = chat.requests[1].messages
    assert [m.content for m in messages if isinstance(m, ToolMessage)] == [STOPPED_CALL]


async def test_a_stream_closed_early_leaves_the_branch_unsettled(
    server_settings: ServerSettings,
):
    chat = ScriptedChat([calls(tool_call("list_tables", {}, "c1")), "Late.", "Next."])
    agent = make_agent(server_settings, chat)
    frames = agent.run(**WHERE, branch="main", turn_id="u1", message="Tables?")
    first = await anext(frames)
    assert isinstance(first, ToolFrame)
    await frames.aclose()
    position = AgentPositions(server_settings.data_dir / "agent.json").branch(
        "t1", "main"
    )
    assert position is not None
    assert position.status == "unsettled"
    assert only(await ask(agent, "u2", "Next?"), FinalFrame)[0].text == "Late."


async def test_threads_survive_a_new_agent(server_settings: ServerSettings):
    await ask(make_agent(server_settings, ScriptedChat(["A1"])), "u1", "first")
    chat = ScriptedChat(["A2", "A1 again"])
    agent = make_agent(server_settings, chat)
    await ask(agent, "u2", "second")
    assert conversation(chat.requests[0]) == [
        ("human", "first"),
        ("ai", "A1"),
        ("human", "second"),
    ]
    await collect(agent.edit(**WHERE, branch="b1", turn_id="u1", message="first!"))
    assert conversation(chat.requests[1]) == [("human", "first!")]
    assert (server_settings.data_dir / "state.jsonl").is_file()


async def test_a_run_killed_mid_way_is_settled_before_the_next_message(
    server_settings: ServerSettings,
):
    positions = server_settings.data_dir / "agent.json"
    chat = ScriptedChat(["A1", calls(tool_call("list_tables", {}, "c9"))])
    agent = make_agent(server_settings, chat)
    await ask(agent, "u1", "first")
    frames = agent.run(**WHERE, branch="main", turn_id="u2", message="Tables?")
    assert isinstance(await anext(frames), ToolFrame)
    at_the_kill = positions.read_bytes()
    await frames.aclose()
    positions.write_bytes(at_the_kill)
    after = ScriptedChat(["After the crash."])
    answer = await ask(make_agent(server_settings, after), "u3", "Next?")
    assert only(answer, FinalFrame)[0].text == "After the crash."
    assert conversation(after.requests[0]) == [
        ("human", "first"),
        ("ai", "A1"),
        ("human", "Next?"),
    ]


async def test_the_step_limit_leaves_room_for_the_whole_tool_budget(
    server_settings: ServerSettings,
):
    rounds = [
        calls(tool_call("list_tables", {}, f"c{n}")) for n in range(MAX_TOOL_CALLS + 1)
    ]
    chat = ScriptedChat([*rounds, "Done."])
    frames = await ask(make_agent(server_settings, chat), "u1", "Tables?")
    finished = [t for t in only(frames, ToolFrame) if t.status == "finished"]
    errors = [t for t in only(frames, ToolFrame) if t.status == "error"]
    assert len(finished) == MAX_TOOL_CALLS
    assert len(errors) == 1
    assert "limit" in (errors[0].summary or "")
    assert only(frames, FinalFrame)[0].text == "Done."


async def test_each_version_of_an_edited_turn_gets_its_own_call_ids(
    server_settings: ServerSettings,
):
    agent = make_agent(server_settings, DemoChat(pace=0, slow_pace=0))
    first = await ask(agent, "u1", "Tables?")
    second = await collect(
        agent.edit(**WHERE, branch="b1", turn_id="u1", message="Tables, again?")
    )
    third = await collect(
        agent.edit(**WHERE, branch="b2", turn_id="u1", message="Tables, once more?")
    )
    ids = [
        {tool.call_id for tool in only(run, ToolFrame)}
        for run in (first, second, third)
    ]
    assert all(ids)
    assert len(ids[0] | ids[1] | ids[2]) == sum(len(run) for run in ids)
