import pytest
from nodestep import (
    ContextNotProvidedError,
    InMemoryStateStore,
    Resume,
    ScriptedChat,
    build_react_agent,
)
from nodestep.chat import AIMessage, HumanMessage, ToolMessage

from harness import call_tool_once, call_tools, calls, earlier_result, tool_call
from text_to_sql_demo.charts import ChartSpec
from text_to_sql_demo.database import Database
from text_to_sql_demo.errors import (
    AskUserAloneError,
    ChartError,
    ForbiddenOperationError,
    SessionContextError,
    StatementNotAllowedError,
    UnknownColumnError,
    UnknownTableError,
)
from text_to_sql_demo.execution import SqlResult
from text_to_sql_demo.safety import SqlValidator
from text_to_sql_demo.schema import DatabaseSchema, TableDescription
from text_to_sql_demo.tools import (
    TOOLS,
    ClarificationAnswer,
    ClarificationRequest,
    DatabaseSession,
    FollowUps,
    TableList,
    ask_user,
    describe_table,
    list_tables,
    make_chart,
    question_key,
    run_sql,
    suggest_follow_ups,
)

PROPOSALS = [
    {"id": "gross", "label": "Gross", "description": "Price times quantity."},
    {"id": "net", "label": "Net", "description": "Gross minus refunds."},
]


@pytest.fixture
def session(database: Database) -> DatabaseSession:
    return DatabaseSession(database, max_rows=10, timeout_ms=1000)


def test_the_tools_have_plain_names():
    assert [tool.name for tool in TOOLS] == [
        "list_tables",
        "describe_table",
        "run_sql",
        "make_chart",
        "ask_user",
        "suggest_follow_ups",
    ]
    assert all(tool.description.strip() for tool in TOOLS)


async def test_list_tables_names_tables_with_row_counts_and_columns(
    session: DatabaseSession,
):
    message = await call_tool_once(list_tables, {}, session)
    listing = TableList.model_validate_json(message.content or "")
    assert listing.database == "fixture"
    assert [(t.name, t.row_count) for t in listing.tables] == [
        ("Course", 3),
        ("Teacher", 3),
        ("Video", 30),
    ]
    assert listing.tables[1].columns == ["TeacherId", "Name"]


async def test_describe_table_shows_samples(session: DatabaseSession):
    message = await call_tool_once(describe_table, {"table": "video"}, session)
    description = TableDescription.model_validate_json(message.content or "")
    assert description.name == "Video"
    assert description.row_count == 30
    assert [column.name for column in description.columns][:2] == ["VideoId", "Name"]
    assert description.columns[1].samples == ["Video 1", "Video 2", "Video 3"]


async def test_describe_table_refuses_an_unknown_table(session: DatabaseSession):
    with pytest.raises(UnknownTableError, match="Tables: Course, Teacher, Video"):
        await call_tool_once(describe_table, {"table": "Student"}, session)


async def test_run_sql_returns_the_formatted_sql_it_ran(
    session: DatabaseSession, schema: DatabaseSchema
):
    written = "select name from teacher order by name;"
    message = await call_tool_once(run_sql, {"sql": written}, session)
    result = SqlResult.model_validate_json(message.content or "")
    assert result.sql == SqlValidator(schema).validate(written)
    assert result.sql.startswith("SELECT\n")
    assert result.rows == [["Ada Byrne"], ["Ben Clarke"], ["Cara Diaz"]]
    assert result.truncated is False


async def test_run_sql_caps_the_rows(session: DatabaseSession):
    message = await call_tool_once(run_sql, {"sql": "SELECT * FROM Video"}, session)
    result = SqlResult.model_validate_json(message.content or "")
    assert result.row_count == 10
    assert result.truncated is True


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("DELETE FROM Teacher", StatementNotAllowedError),
        ("SELECT Title, Nope FROM Course", UnknownColumnError),
        ("SELECT * FROM Student", UnknownTableError),
        ("SELECT load_extension('x')", ForbiddenOperationError),
    ],
)
async def test_run_sql_raises_the_checks_errors(
    session: DatabaseSession, sql: str, error: type[Exception]
):
    with pytest.raises(error):
        await call_tool_once(run_sql, {"sql": sql}, session)


async def result_of(session: DatabaseSession, sql: str) -> SqlResult:
    message = await call_tool_once(run_sql, {"sql": sql}, session)
    return SqlResult.model_validate_json(message.content or "")


async def test_make_chart_draws_the_last_result(session: DatabaseSession):
    older = await result_of(session, "SELECT Name AS name, 1 AS n FROM Teacher")
    latest = await result_of(
        session,
        "SELECT TeacherId AS teacher_id, count(*) AS courses FROM Course "
        "GROUP BY TeacherId ORDER BY TeacherId",
    )
    message = await call_tool_once(
        make_chart,
        {"kind": "bar", "title": "Courses", "x": "teacher_id", "y": "courses"},
        session,
        before=[*earlier_result(older, "c0"), *earlier_result(latest, "c9")],
    )
    chart = ChartSpec.model_validate_json(message.content or "")
    assert chart.kind == "bar"
    assert chart.x.label == "Teacher id"
    assert chart.y.label == "Courses"
    assert chart.data == [
        {"teacher_id": 1, "courses": 2},
        {"teacher_id": 2, "courses": 1},
    ]


async def test_make_chart_takes_labels(session: DatabaseSession):
    result = await result_of(
        session, "SELECT Name AS name, TeacherId AS n FROM Teacher"
    )
    message = await call_tool_once(
        make_chart,
        {
            "kind": "line",
            "title": "T",
            "x": "name",
            "y": "n",
            "x_label": "Teacher",
            "y_label": "Number",
        },
        session,
        before=earlier_result(result),
    )
    chart = ChartSpec.model_validate_json(message.content or "")
    assert (chart.x.label, chart.y.label) == ("Teacher", "Number")


async def test_make_chart_names_the_columns_it_can_use(session: DatabaseSession):
    result = await result_of(
        session, "SELECT Name AS name, TeacherId AS n FROM Teacher"
    )
    with pytest.raises(ChartError, match="Columns: name, n"):
        await call_tool_once(
            make_chart,
            {"kind": "bar", "title": "T", "x": "name", "y": "count"},
            session,
            before=earlier_result(result),
        )


async def test_make_chart_needs_numbers_on_the_y_axis(session: DatabaseSession):
    result = await result_of(
        session, "SELECT Name AS name, TeacherId AS n FROM Teacher"
    )
    with pytest.raises(ChartError, match="numbers"):
        await call_tool_once(
            make_chart,
            {"kind": "bar", "title": "T", "x": "n", "y": "name"},
            session,
            before=earlier_result(result),
        )


async def test_make_chart_needs_a_result_first(session: DatabaseSession):
    with pytest.raises(ChartError, match="run_sql"):
        await call_tool_once(
            make_chart, {"kind": "bar", "title": "T", "x": "a", "y": "b"}, session
        )


async def test_ask_user_pauses_with_the_question_and_returns_the_answer(
    session: DatabaseSession,
):
    arguments = {"question": "Which revenue?", "proposals": PROPOSALS}
    chat = ScriptedChat([calls(tool_call("ask_user", arguments)), "done"])
    graph = build_react_agent(
        chat, tools=[ask_user], tool_errors="raise", state_store=InMemoryStateStore()
    )
    paused = await graph.ainvoke(
        {"messages": [HumanMessage(content="q")]}, thread_id="t", context=session
    )
    assert paused.status == "interrupted"
    [(key, pending)] = paused.interrupts.items()
    assert pending.id == "clarify"
    assert key.endswith(":clarify")
    request = ClarificationRequest.model_validate(pending.payload)
    assert request.question == "Which revenue?"
    assert [proposal.id for proposal in request.proposals] == ["gross", "net"]
    assert request.allow_free_text is True
    done = await graph.ainvoke(
        None, thread_id="t", resume=Resume(answers={key: "net"}), context=session
    )
    [message] = [m for m in done.state.messages if isinstance(m, ToolMessage)]
    answer = ClarificationAnswer.model_validate_json(message.content or "")
    assert answer.answer == "net"
    assert answer.proposal is not None
    assert answer.proposal.label == "Net"


async def test_ask_user_passes_free_text_on(session: DatabaseSession):
    arguments = {"question": "Q?", "proposals": PROPOSALS, "allow_free_text": True}
    chat = ScriptedChat([calls(tool_call("ask_user", arguments)), "done"])
    graph = build_react_agent(
        chat, tools=[ask_user], tool_errors="raise", state_store=InMemoryStateStore()
    )
    paused = await graph.ainvoke(
        {"messages": [HumanMessage(content="q")]}, thread_id="t", context=session
    )
    [key] = paused.interrupts
    done = await graph.ainvoke(
        None,
        thread_id="t",
        resume=Resume(answers={key: "after refunds"}),
        context=session,
    )
    [message] = [m for m in done.state.messages if isinstance(m, ToolMessage)]
    answer = ClarificationAnswer.model_validate_json(message.content or "")
    assert (answer.answer, answer.proposal) == ("after refunds", None)


@pytest.mark.parametrize(
    "proposals",
    [
        PROPOSALS[:1],
        [*PROPOSALS, *[{**PROPOSALS[0], "id": f"p{n}"} for n in range(3)]],
        [PROPOSALS[0], PROPOSALS[0]],
    ],
)
async def test_ask_user_needs_two_to_four_distinct_proposals(
    session: DatabaseSession, proposals: list[dict[str, str]]
):
    with pytest.raises(Exception, match="proposal"):
        await call_tool_once(
            ask_user, {"question": "Q?", "proposals": proposals}, session
        )


async def test_ask_user_must_be_called_alone(session: DatabaseSession):
    first = calls(
        tool_call("ask_user", {"question": "Q?", "proposals": PROPOSALS}, "c1"),
        tool_call("list_tables", {}, "c2"),
    )
    with pytest.raises(AskUserAloneError, match="on its own"):
        await call_tools([ask_user, list_tables], first, session)


async def test_suggest_follow_ups_takes_two_or_three_questions(
    session: DatabaseSession,
):
    message = await call_tool_once(
        suggest_follow_ups, {"questions": ["A?", "B?"]}, session
    )
    assert FollowUps.model_validate_json(message.content or "").questions == [
        "A?",
        "B?",
    ]
    for questions in (["A?"], ["A?", "B?", "C?", "D?"]):
        with pytest.raises(Exception, match="questions"):
            await call_tool_once(suggest_follow_ups, {"questions": questions}, session)


async def test_suggest_follow_ups_leaves_out_questions_already_asked(
    session: DatabaseSession,
):
    before = [
        HumanMessage(content="Which store has the most returns?"),
        AIMessage(content="Lisbon."),
    ]
    message = await call_tool_once(
        suggest_follow_ups,
        {"questions": ["which store has  the most RETURNS", "Question.", "New one?"]},
        session,
        before=before,
    )
    assert FollowUps.model_validate_json(message.content or "").questions == [
        "New one?"
    ]


def test_question_keys_ignore_case_spacing_and_end_punctuation():
    assert question_key("  Which ten  products sold the MOST units ? ") == question_key(
        "which ten products sold the most units"
    )
    assert question_key("Revenue per month?") != question_key("Revenue per year?")


async def test_a_tool_without_the_session_raises():
    with pytest.raises(ContextNotProvidedError):
        await call_tool_once(list_tables, {}, None)


async def test_a_context_that_is_no_session_fails_the_run_instead_of_reaching_the_model():
    chat = ScriptedChat([calls(tool_call("list_tables", {})), "done"])
    graph = build_react_agent(chat, tools=[list_tables], tool_errors="return")
    with pytest.raises(SessionContextError, match="got object"):
        await graph.ainvoke(
            {"messages": [HumanMessage(content="question")]}, context=object()
        )
    assert len(chat.requests) == 1


def test_the_session_reads_the_schema_once(session: DatabaseSession):
    assert session.schema() is session.schema()
    assert session.schema().database == "fixture"
