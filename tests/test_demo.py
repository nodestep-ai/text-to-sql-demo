import asyncio
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import closing
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import sqlglot
from nodestep.chat import ChatRequest, ChatStreamChunk
from pydantic import TypeAdapter

from live import LiveStream
from make_demo_db import SIDECAR, DemoShopBuilder
from text_to_sql_demo.app import create_app
from text_to_sql_demo.demo import (
    CATEGORY_REVENUE,
    DEMO_DATABASE,
    QUESTIONS,
    REPORT,
    REPORT_PRODUCTS_SQL,
    REPORT_RETURNS_SQL,
    REPORT_SQL,
    REPORT_TOPICS,
    RETURNS_PER_STORE,
    REVENUE_PER_MONTH,
    TOP_PRODUCTS,
    DemoChat,
    DemoTurn,
)
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
from text_to_sql_demo.memory import KEY_LENGTH, TITLE_LENGTH, MemoryStore, MemoryText
from text_to_sql_demo.nodestep_agent import NodestepAgent
from text_to_sql_demo.registry import DatabaseRegistry
from text_to_sql_demo.safety import SqlLayoutGenerator
from text_to_sql_demo.settings import ServerSettings

WHERE = {"thread_id": "t1", "database": DEMO_DATABASE, "branch": "main"}
FREE_TEXT = "whatever you think"


@pytest.fixture(scope="module")
def databases(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("data") / "databases"
    folder.mkdir()
    DemoShopBuilder().build(folder / f"{DEMO_DATABASE}.sqlite")
    return folder


def demo_settings(databases: Path, data_dir: Path) -> ServerSettings:
    data_dir.mkdir(parents=True, exist_ok=True)
    link = data_dir / "databases"
    if not link.exists():
        link.symlink_to(databases, target_is_directory=True)
    return ServerSettings.model_validate(
        {
            "TEXT_TO_SQL_DEMO_DATA_DIR": str(data_dir),
            "TEXT_TO_SQL_DEMO_WEB_DIST": str(data_dir / "no-dist"),
        }
    )


def demo_agent(settings: ServerSettings, chat: DemoChat) -> NodestepAgent:
    return NodestepAgent.from_settings(
        chat, settings, model="scripted", provider_name="scripted"
    )


@pytest.fixture
def agent(databases: Path, tmp_path: Path) -> NodestepAgent:
    settings = demo_settings(databases, tmp_path / "data")
    return demo_agent(settings, DemoChat(pace=0, slow_pace=0, analyst_pace=0))


def query(databases: Path, sql: str) -> list[list[object]]:
    path = databases / f"{DEMO_DATABASE}.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        return [list(row) for row in connection.execute(sql)]


async def collect(frames: AsyncIterator[Frame]) -> list[Frame]:
    return [frame async for frame in frames]


async def ask(
    agent: NodestepAgent,
    message: str,
    *,
    thread_id: str | None = None,
    turn_id: str = "u1",
) -> list[Frame]:
    return await collect(
        agent.run(
            thread_id=thread_id or uuid.uuid4().hex,
            database=DEMO_DATABASE,
            branch="main",
            turn_id=turn_id,
            message=message,
        )
    )


async def answer(agent: NodestepAgent, reply: str, *, thread_id: str) -> list[Frame]:
    [question] = only(
        await ask(agent, CATEGORY_REVENUE, thread_id=thread_id), ClarificationFrame
    )
    return await collect(
        agent.resume(
            thread_id=thread_id,
            database=DEMO_DATABASE,
            branch="main",
            turn_id="u2",
            key=question.key,
            answer=reply,
        )
    )


def only[F](frames: list[Frame], kind: type[F]) -> list[F]:
    return [frame for frame in frames if isinstance(frame, kind)]


def check_answer(
    frames: list[Frame], *, lists_tables: bool = True
) -> tuple[SqlResultFrame, ChartFrame, str]:
    TypeAdapter(list[Frame]).validate_python(frames)
    kinds = [frame.type for frame in frames]
    assert kinds[:2] == ["tool", "tool"]
    assert kinds[-2:] == ["final", "suggestions"]
    assert "token" in kinds
    tools = only(frames, ToolFrame)
    names = {"run_sql", "make_chart"}
    if lists_tables:
        listing = [tool for tool in tools if tool.name == "list_tables"]
        assert tools[0] is listing[0]
        assert [tool.status for tool in listing] == ["started", "finished"]
        assert listing[1].summary is not None
        assert listing[1].summary.startswith("8 tables: categories, customers")
        names.add("list_tables")
    if any(tool.name == "search_memory" for tool in tools):
        names.add("search_memory")
    assert {tool.name for tool in tools} == names
    [result] = only(frames, SqlResultFrame)
    [chart] = only(frames, ChartFrame)
    [final] = only(frames, FinalFrame)
    assert "".join(token.text for token in only(frames, TokenFrame)) == final.text
    [suggestions] = only(frames, SuggestionsFrame)
    assert set(suggestions.questions) <= set(QUESTIONS)
    assert 2 <= len(suggestions.questions) <= 3
    assert not only(frames, UsageFrame)
    return result, chart, final.text


def check_report(frames: list[Frame]) -> tuple[list[SqlResultFrame], str]:
    TypeAdapter(list[Frame]).validate_python(frames)
    tools = only(frames, ToolFrame)
    assert [(tool.name, tool.status) for tool in tools] == [
        ("load_skill", "started"),
        ("search_memory", "started"),
        ("load_skill", "finished"),
        ("search_memory", "finished"),
        ("analyze_topics", "started"),
        ("analyze_topics", "finished"),
    ]
    assert tools[0].arguments == {"name": "report"}
    assert tools[1].arguments == {"query": "revenue"}
    assert tools[4].arguments == {
        "topics": [topic.model_dump() for topic in REPORT_TOPICS]
    }
    assert tools[5].summary == "revenue, returns, top products"
    results = only(frames, SqlResultFrame)
    assert len(results) == 3
    assert not only(frames, ChartFrame)
    [final] = only(frames, FinalFrame)
    assert "".join(token.text for token in only(frames, TokenFrame)) == final.text
    return results, final.text


def answer_text(frames: list[Frame], question: str) -> str:
    if "report" in question.lower():
        return check_report(frames)[1]
    return check_answer(frames)[2]


async def test_top_products(agent: NodestepAgent, databases: Path):
    result, chart, text = check_answer(await ask(agent, TOP_PRODUCTS))
    expected = query(
        databases,
        "SELECT p.name, sum(i.quantity) AS units FROM order_items i "
        "JOIN products p ON p.product_id = i.product_id "
        "JOIN orders o ON o.order_id = i.order_id WHERE o.status = 'completed' "
        "GROUP BY p.product_id ORDER BY units DESC, p.name LIMIT 10",
    )
    assert result.rows == expected
    assert result.columns == ["product", "units"]
    assert chart.kind == "bar"
    assert chart.data[0] == {"product": expected[0][0], "units": expected[0][1]}
    assert str(expected[0][0]) in text
    assert str(expected[0][1]) in text


async def test_revenue_per_month(agent: NodestepAgent):
    result, chart, text = check_answer(await ask(agent, "revenue per month please"))
    assert result.columns == ["month", "revenue"]
    assert [row[0] for row in result.rows][:2] == ["2024-01", "2024-02"]
    assert result.rows[-1][0] == "2025-12"
    assert result.row_count == 24
    assert chart.kind == "line"
    assert len(chart.data) == 24
    best = max(result.rows, key=lambda row: float(str(row[1])))
    assert str(best[0]) in text


async def test_returns_per_store_corrects_a_failed_query(agent: NodestepAgent):
    frames = await ask(agent, RETURNS_PER_STORE)
    result, chart, text = check_answer(frames)
    sql_tools = [tool for tool in only(frames, ToolFrame) if tool.name == "run_sql"]
    assert [tool.status for tool in sql_tools] == [
        "started",
        "error",
        "started",
        "finished",
    ]
    failure = sql_tools[1].summary
    assert failure is not None
    assert failure.startswith("Unknown column: order_id.")
    assert "returns(return_id, order_item_id" in failure
    assert sql_tools[0].call_id != sql_tools[2].call_id
    assert result.call_id == sql_tools[2].call_id
    assert result.rows[0] == ["Online shop", 44]
    assert chart.kind == "bar"
    assert "Online shop" in text


async def test_category_revenue_asks_first(agent: NodestepAgent):
    frames = await ask(agent, CATEGORY_REVENUE)
    assert frames[-1].type == "clarification"
    [question] = only(frames, ClarificationFrame)
    assert [proposal.id for proposal in question.proposals] == ["gross", "net", "share"]
    assert question.allow_free_text is True
    assert all(proposal.description for proposal in question.proposals)
    assert not only(frames, FinalFrame)


@pytest.mark.parametrize(
    ("reply", "column", "label"),
    [
        ("gross", "revenue", "Gross revenue"),
        ("net", "revenue", "Net of refunds"),
        ("share", "share", "Share of total"),
    ],
)
async def test_a_chosen_proposal_is_named_once(
    agent: NodestepAgent, reply: str, column: str, label: str
):
    frames = await answer(agent, reply, thread_id="t1")
    result, chart, text = check_answer(frames, lists_tables=False)
    assert result.columns == ["category", column]
    assert result.rows[0][0] == "Coffee"
    assert chart.y.field == column
    assert text.startswith(f"{label}: Coffee ")
    assert "I read" not in text


@pytest.mark.parametrize(
    ("reply", "column", "reading"),
    [
        ("after refunds, please", "revenue", "revenue net of refunds"),
        ("as a percentage", "share", "each category's share of the total"),
        ("gross sales", "revenue", "gross revenue"),
    ],
)
async def test_free_text_is_echoed_with_the_reading_used(
    agent: NodestepAgent, reply: str, column: str, reading: str
):
    frames = await answer(agent, reply, thread_id="t1")
    result, _, text = check_answer(frames, lists_tables=False)
    assert result.columns == ["category", column]
    assert text.startswith(f'I read "{reply}" as {reading}. Coffee ')
    assert text.lower().count(reading.lower()) == 1
    assert "could not apply" not in text


async def test_free_text_that_names_no_reading_says_which_one_was_used(
    agent: NodestepAgent,
):
    frames = await answer(agent, FREE_TEXT, thread_id="t1")
    result, _, text = check_answer(frames, lists_tables=False)
    assert result.columns == ["category", "revenue"]
    assert text.startswith(
        f'"{FREE_TEXT}" does not say how to count revenue, so I used gross revenue. '
    )


@pytest.mark.parametrize(
    ("reply", "named"),
    [
        ("net revenue for 2025, online orders only", '"2025" and "online"'),
        ("share in Q3 of last year", '"Q3" and "last year"'),
        (
            "gross for March, April and in-store sales",
            '"March", "April" and "in-store"',
        ),
    ],
)
async def test_free_text_names_the_constraints_the_demo_could_not_apply(
    agent: NodestepAgent, reply: str, named: str
):
    frames = await answer(agent, reply, thread_id="t1")
    _, _, text = check_answer(frames, lists_tables=False)
    assert (
        f"I could not apply {named} in this demo, so the figures cover all "
        "completed orders." in text
    )


@pytest.mark.parametrize("reply", ["internet sales", "the network of shops"])
async def test_a_hint_inside_another_word_is_not_a_reading(
    agent: NodestepAgent, reply: str
):
    frames = await answer(agent, reply, thread_id="t1")
    result, _, text = check_answer(frames, lists_tables=False)
    assert result.columns == ["category", "revenue"]
    assert text.startswith(
        f'"{reply}" does not say how to count revenue, so I used gross revenue. '
    )


@pytest.mark.parametrize(
    ("reply", "column", "reading"),
    [
        ("gross, not net", "revenue", "gross revenue"),
        ("not gross, net of refunds", "revenue", "revenue net of refunds"),
        ("share of the total, not net", "share", "each category's share of the total"),
        ("revenue, not counting refunds", "revenue", "gross revenue"),
        ("not including returns", "revenue", "gross revenue"),
        ("before refunds", "revenue", "gross revenue"),
        ("ignoring the refunds", "revenue", "gross revenue"),
        ("not gross", "revenue", "revenue net of refunds"),
        ("not gross and not net", "share", "each category's share of the total"),
    ],
)
async def test_a_negated_reading_is_not_used(
    agent: NodestepAgent, reply: str, column: str, reading: str
):
    frames = await answer(agent, reply, thread_id="t1")
    result, _, text = check_answer(frames, lists_tables=False)
    assert result.columns == ["category", column]
    assert text.startswith(f'I read "{reply}" as {reading}. ')


async def test_the_verb_may_is_not_a_month(agent: NodestepAgent):
    reply = "whatever you think may be best"
    frames = await answer(agent, reply, thread_id="t1")
    _, _, text = check_answer(frames, lists_tables=False)
    assert "could not apply" not in text


@pytest.mark.parametrize(
    ("reply", "named"),
    [
        ("net for May 2025", '"May" and "2025"'),
        ("net in may", '"may"'),
        ("gross since May", '"May"'),
    ],
)
async def test_the_month_may_is_a_constraint(
    agent: NodestepAgent, reply: str, named: str
):
    frames = await answer(agent, reply, thread_id="t1")
    _, _, text = check_answer(frames, lists_tables=False)
    assert f"I could not apply {named} in this demo" in text


async def test_an_unapplied_constraint_leaves_the_query_unchanged(
    agent: NodestepAgent,
):
    plain = await answer(agent, "net", thread_id="t-plain")
    limited = await answer(agent, "net revenue for 2025 only", thread_id="t-limited")
    assert only(limited, SqlResultFrame)[0].rows == only(plain, SqlResultFrame)[0].rows


async def test_the_three_answers_differ(agent: NodestepAgent):
    rows = []
    for reply in ("gross", "net", "share"):
        frames = await answer(agent, reply, thread_id=f"t-{reply}")
        rows.append(only(frames, SqlResultFrame)[0].rows)
    gross, net, share = (dict(map(tuple, result)) for result in rows)
    assert set(gross) == set(net) == set(share)
    assert all(gross[name] >= net[name] for name in gross)
    assert any(gross[name] > net[name] for name in gross)
    assert round(sum(float(str(value)) for value in share.values())) == 100


async def test_an_unknown_question_shows_the_tables(agent: NodestepAgent):
    frames = await ask(agent, "What is the meaning of life?")
    TypeAdapter(list[Frame]).validate_python(frames)
    [result] = only(frames, SqlResultFrame)
    assert result.columns == ["table_name", "row_count"]
    assert [row[0] for row in result.rows] == [
        "categories",
        "customers",
        "order_items",
        "orders",
        "products",
        "returns",
        "staff",
        "stores",
    ]
    [final] = only(frames, FinalFrame)
    assert "scripted demo" in final.text
    [suggestions] = only(frames, SuggestionsFrame)
    assert len(suggestions.questions) == 3


async def test_every_question_has_its_own_answer(agent: NodestepAgent):
    first_sql = set()
    for question in QUESTIONS:
        frames = await ask(agent, question)
        if question == CATEGORY_REVENUE:
            assert not only(frames, SqlResultFrame)
            continue
        first_sql.add(only(frames, SqlResultFrame)[0].sql)
    assert len(first_sql) == len(QUESTIONS) - 1


async def test_every_query_shows_the_formatted_sql_it_ran(
    agent: NodestepAgent, databases: Path
):
    for question in [*QUESTIONS, "What is the meaning of life?"]:
        frames = await ask(agent, question)
        started = [
            tool
            for tool in only(frames, ToolFrame)
            if tool.name == "run_sql" and tool.status == "started"
        ]
        results = {result.call_id: result for result in only(frames, SqlResultFrame)}
        for tool in started:
            if tool.call_id not in results:
                continue
            assert tool.arguments is not None
            written = str(tool.arguments["sql"])
            result = results[tool.call_id]
            tree = sqlglot.parse_one(written, read="sqlite")
            layout = SqlLayoutGenerator(pretty=True, dialect="sqlite")
            assert result.sql == layout.generate(tree)
            assert sqlglot.parse_one(result.sql, read="sqlite") == tree
            assert result.rows == query(databases, written)


async def test_call_ids_stay_unique_after_a_clarification(agent: NodestepAgent):
    asked = await ask(agent, CATEGORY_REVENUE, thread_id="t1")
    [question] = only(asked, ClarificationFrame)
    answered = await collect(
        agent.resume(**WHERE, turn_id="u2", key=question.key, answer="net")
    )
    assert {tool.name for tool in only(asked, ToolFrame)} == {
        "list_tables",
        "search_memory",
    }
    asked_ids = {tool.call_id for tool in only(asked, ToolFrame)}
    answered_ids = {tool.call_id for tool in only(answered, ToolFrame)}
    assert asked_ids
    assert answered_ids
    assert not asked_ids & answered_ids
    assert {frame.call_id for frame in only(answered, SqlResultFrame)} <= answered_ids


async def test_call_ids_differ_between_turns(agent: NodestepAgent):
    first = await collect(agent.run(**WHERE, turn_id="u1", message=TOP_PRODUCTS))
    second = await collect(agent.run(**WHERE, turn_id="u3", message=TOP_PRODUCTS))
    first_ids = {tool.call_id for tool in only(first, ToolFrame)}
    second_ids = {tool.call_id for tool in only(second, ToolFrame)}
    assert len(first_ids) == 3
    assert not first_ids & second_ids


def test_the_demo_sidecar_lists_every_demo_question(databases: Path):
    assert SIDECAR["examples"] == list(QUESTIONS)
    assert REPORT in QUESTIONS
    assert len(QUESTIONS) == 5
    [info] = DatabaseRegistry(databases).databases()
    assert info.examples == list(QUESTIONS)


async def test_an_edit_answers_the_new_message(agent: NodestepAgent):
    await ask(agent, TOP_PRODUCTS, thread_id="t1")
    frames = await collect(
        agent.edit(
            thread_id="t1",
            database=DEMO_DATABASE,
            branch="b1",
            turn_id="u1",
            message=REVENUE_PER_MONTH,
        )
    )
    _, chart, _ = check_answer(frames)
    assert chart.kind == "line"


async def test_the_report_loads_its_skill_and_asks_one_sub_agent_per_topic(
    agent: NodestepAgent, databases: Path
):
    frames = await ask(agent, REPORT)
    results, text = check_report(frames)
    expected = [
        query(databases, REPORT_SQL),
        query(databases, REPORT_RETURNS_SQL),
        query(databases, REPORT_PRODUCTS_SQL),
    ]
    assert [result.rows for result in results] == expected
    assert [result.columns for result in results] == [
        ["store", "orders", "revenue"],
        ["store", "returns", "refunds"],
        ["product", "units"],
    ]
    paragraphs = text.split("\n\n")
    assert [paragraph.split(":")[0] for paragraph in paragraphs[1:]] == [
        "Revenue",
        "Returns",
        "Top products",
    ]
    assert f"{expected[0][0][0]} alone brings in" in paragraphs[1]
    assert str(expected[1][0][0]) in paragraphs[2]
    assert str(expected[2][0][0]) in paragraphs[3]
    assert len(only(frames, TokenFrame)) > 60


async def test_the_report_is_written_from_the_answers_of_the_sub_agents(
    agent: NodestepAgent, databases: Path
):
    frames = await ask(agent, REPORT)
    _, text = check_report(frames)
    [revenue] = query(databases, REPORT_SQL)[:1]
    store, orders, amount = revenue
    assert f"Revenue: {store} took {orders} orders worth €{amount:,.2f}." in text


async def test_the_report_sub_agents_answer_with_their_own_queries(
    agent: NodestepAgent,
):
    frames = await ask(agent, REPORT, thread_id="t-report")
    [finished] = [
        tool
        for tool in only(frames, ToolFrame)
        if tool.name == "analyze_topics" and tool.status == "finished"
    ]
    results = only(frames, SqlResultFrame)
    assert {result.call_id.rsplit("/", 2)[0] for result in results} == {
        finished.call_id
    }
    assert len({result.call_id for result in results}) == 3


LABEL: ContextVar[str] = ContextVar("label", default="main")


class LabelledChat(DemoChat):
    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        turn = DemoTurn(request)
        LABEL.set(turn.message if turn.analyst else "main")
        async for chunk in super().stream(request):
            yield chunk


async def test_the_report_takes_ten_to_fifteen_seconds_at_the_default_pace(
    databases: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    slept: dict[str, float] = {}

    async def sleep(seconds: float) -> None:
        slept[LABEL.get()] = slept.get(LABEL.get(), 0.0) + seconds

    monkeypatch.setattr("text_to_sql_demo.demo.asyncio", SimpleNamespace(sleep=sleep))
    settings = demo_settings(databases, tmp_path / "data")
    await ask(demo_agent(settings, LabelledChat()), REPORT)
    analysts = [seconds for label, seconds in slept.items() if label != "main"]
    assert len(analysts) == 3
    assert 3 <= max(analysts) <= 5
    assert 10 <= slept["main"] + max(analysts) <= 15


async def stop_report(
    databases: Path, tmp_path: Path, stop_at: str, chat: DemoChat
) -> tuple[list[tuple[str, dict]], dict]:
    settings = demo_settings(databases, tmp_path / "data")
    app = create_app(settings, demo_agent(settings, chat))
    transport = httpx.ASGITransport(app=app)
    body = {"thread_id": None, "database": DEMO_DATABASE, "message": REPORT}
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
        LiveStream(app, "/api/chat", body) as stream,
    ):
        kind, run = await stream.event()
        data: dict = {}
        while kind != "token" and not (
            kind == "tool"
            and data.get("name") == stop_at
            and data.get("status") == "started"
        ):
            kind, data = await stream.event()
        stop = await http.post(f"/api/threads/{run['thread_id']}/stop")
        assert stop.status_code == 204
        rest = await stream.rest()
        detail = (await http.get(f"/api/threads/{run['thread_id']}")).json()
    return rest, detail


async def test_the_report_can_be_stopped_through_the_api(
    databases: Path, tmp_path: Path
):
    chat = DemoChat(pace=0, slow_pace=60, analyst_pace=0)
    rest, detail = await stop_report(databases, tmp_path, "", chat)
    assert rest == [("stopped", {}), ("done", {})]
    turn = detail["turns"][1]
    assert turn["status"] == "stopped"
    kinds = [frame["type"] for frame in turn["frames"]]
    assert kinds.count("sql_result") == 3
    assert turn["text"].startswith("Sales")


async def test_stopping_the_report_stops_its_sub_agents(
    databases: Path, tmp_path: Path
):
    chat = DemoChat(pace=0, slow_pace=0, analyst_pace=60)
    rest, detail = await stop_report(databases, tmp_path, "analyze_topics", chat)
    assert rest == [("stopped", {}), ("done", {})]
    turn = detail["turns"][1]
    assert turn["status"] == "stopped"
    assert [
        (frame["name"], frame["status"])
        for frame in turn["frames"]
        if frame["type"] == "tool"
    ] == [
        ("load_skill", "started"),
        ("search_memory", "started"),
        ("load_skill", "finished"),
        ("search_memory", "finished"),
        ("analyze_topics", "started"),
    ]
    running = [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("agent:") and not task.done()
    ]
    assert running == []


async def test_suggestions_never_repeat_a_question_asked_in_the_thread(
    agent: NodestepAgent,
):
    asked: list[str] = []
    turns = [TOP_PRODUCTS, REVENUE_PER_MONTH, REPORT, RETURNS_PER_STORE]
    for number, question in enumerate(turns):
        frames = await ask(
            agent, question, thread_id="t1", turn_id=f"u{2 * number + 1}"
        )
        asked.append(question)
        suggestions = only(frames, SuggestionsFrame)
        offered = [q for frame in suggestions for q in frame.questions]
        assert not set(offered) & set(asked), (question, offered)
        if len(set(QUESTIONS) - set(asked)) >= 2:
            assert 2 <= len(offered) <= 3
        else:
            assert not suggestions


async def test_suggestions_skip_a_question_asked_in_other_words(agent: NodestepAgent):
    await ask(agent, "  which TEN products sold the most units ", thread_id="t1")
    frames = await ask(agent, REVENUE_PER_MONTH, thread_id="t1", turn_id="u3")
    [suggestions] = only(frames, SuggestionsFrame)
    assert TOP_PRODUCTS not in suggestions.questions
    assert 2 <= len(suggestions.questions) <= 3


async def test_the_overview_without_suggestions_left_still_answers(
    agent: NodestepAgent,
):
    turns = [TOP_PRODUCTS, REVENUE_PER_MONTH, RETURNS_PER_STORE, REPORT]
    for number, question in enumerate(turns):
        await ask(agent, question, thread_id="t1", turn_id=f"u{2 * number + 1}")
    frames = await ask(agent, "Hello?", thread_id="t1", turn_id="u11")
    [final] = only(frames, FinalFrame)
    assert "scripted demo" in final.text
    assert not only(frames, SuggestionsFrame)


@pytest.mark.parametrize(
    ("question", "named", "scope"),
    [
        (
            "Which ten products sold the most units in 2025?",
            '"2025"',
            "all completed orders",
        ),
        ("What is the revenue per month in 2024?", '"2024"', "all completed orders"),
        ("Which store has the most returns last year?", '"last year"', "all returns"),
        (
            "Write a report on sales by store in 2024.",
            '"2024"',
            "completed orders in 2025",
        ),
    ],
)
async def test_a_question_names_the_constraints_the_demo_could_not_apply(
    agent: NodestepAgent, question: str, named: str, scope: str
):
    text = answer_text(await ask(agent, question), question)
    assert text.startswith(
        f"I could not apply {named} in this demo, so the figures cover {scope}. "
    )


async def test_the_prepared_questions_need_no_caveat(agent: NodestepAgent):
    for question in QUESTIONS:
        if question == CATEGORY_REVENUE:
            continue
        text = answer_text(await ask(agent, question), question)
        assert "could not apply" not in text, question


async def test_a_year_in_the_category_question_is_named_after_a_proposal(
    agent: NodestepAgent,
):
    thread_id = "t-year"
    [question] = only(
        await ask(agent, f"{CATEGORY_REVENUE[:-1]} in 2025?", thread_id=thread_id),
        ClarificationFrame,
    )
    frames = await collect(
        agent.resume(
            thread_id=thread_id,
            database=DEMO_DATABASE,
            branch="main",
            turn_id="u2",
            key=question.key,
            answer="net",
        )
    )
    _, _, text = check_answer(frames, lists_tables=False)
    assert text.startswith(
        'I could not apply "2025" in this demo, so the figures cover all completed '
        "orders. Net of refunds: Coffee "
    )


async def test_a_year_in_the_category_question_is_named_with_the_free_text(
    agent: NodestepAgent,
):
    thread_id = "t-year-text"
    [question] = only(
        await ask(agent, f"{CATEGORY_REVENUE[:-1]} in 2025?", thread_id=thread_id),
        ClarificationFrame,
    )
    frames = await collect(
        agent.resume(
            thread_id=thread_id,
            database=DEMO_DATABASE,
            branch="main",
            turn_id="u2",
            key=question.key,
            answer="net for May 2025",
        )
    )
    _, _, text = check_answer(frames, lists_tables=False)
    assert text.startswith(
        'I read "net for May 2025" as revenue net of refunds. I could not apply '
        '"2025" and "May" in this demo'
    )


async def test_the_last_prepared_question_is_named_when_one_is_left(
    agent: NodestepAgent,
):
    turns = [TOP_PRODUCTS, REVENUE_PER_MONTH, RETURNS_PER_STORE, REPORT]
    for number, question in enumerate(turns):
        frames = await ask(
            agent, question, thread_id="t1", turn_id=f"u{2 * number + 1}"
        )
    [final] = only(frames, FinalFrame)
    assert not only(frames, SuggestionsFrame)
    assert final.text.endswith(f' One prepared question is left: "{CATEGORY_REVENUE}"')


async def test_the_overview_names_the_last_prepared_question(agent: NodestepAgent):
    turns = [TOP_PRODUCTS, REVENUE_PER_MONTH, RETURNS_PER_STORE, REPORT]
    for number, question in enumerate(turns):
        await ask(agent, question, thread_id="t1", turn_id=f"u{2 * number + 1}")
    frames = await ask(agent, "Hello?", thread_id="t1", turn_id="u11")
    [final] = only(frames, FinalFrame)
    assert final.text.endswith(f' One prepared question is left: "{CATEGORY_REVENUE}"')
    assert "Try one of the questions below" not in final.text


async def test_no_note_once_every_prepared_question_was_asked(agent: NodestepAgent):
    for number, question in enumerate(QUESTIONS):
        frames = await ask(
            agent, question, thread_id="t1", turn_id=f"u{2 * number + 1}"
        )
        if question == CATEGORY_REVENUE:
            [clarification] = only(frames, ClarificationFrame)
            await collect(
                agent.resume(
                    thread_id="t1",
                    database=DEMO_DATABASE,
                    branch="main",
                    turn_id=f"u{2 * number + 2}",
                    key=clarification.key,
                    answer="gross",
                )
            )
    frames = await ask(agent, TOP_PRODUCTS, thread_id="t1", turn_id="u20")
    [final] = only(frames, FinalFrame)
    assert "prepared question is left" not in final.text


RULE = "Remember that revenue counts completed orders only."
RULE_NOTE = "From memory: revenue counts completed orders only. "


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return tmp_path / "data"


async def test_remember_saves_the_fact_to_memory(agent: NodestepAgent, data: Path):
    frames = await ask(agent, RULE)
    tools = only(frames, ToolFrame)
    assert [(tool.name, tool.status) for tool in tools] == [
        ("save_memory", "started"),
        ("save_memory", "finished"),
    ]
    assert tools[0].arguments == {
        "key": "revenue-counts-completed-orders-only",
        "title": "Revenue counts completed orders only",
        "content": "Revenue counts completed orders only.",
    }
    assert tools[1].summary == "Revenue counts completed orders only"
    [final] = only(frames, FinalFrame)
    assert final.text == "I will remember that revenue counts completed orders only."
    assert not only(frames, SuggestionsFrame)
    [entry] = MemoryStore(data).entries().memories
    assert entry.content == "Revenue counts completed orders only."


async def test_remembering_the_same_fact_twice_keeps_one_memory(
    agent: NodestepAgent, data: Path
):
    await ask(agent, RULE, thread_id="t1")
    await ask(agent, RULE.lower(), thread_id="t1", turn_id="u3")
    assert len(MemoryStore(data).entries().memories) == 1


async def test_remember_without_a_fact_asks_for_one(agent: NodestepAgent):
    frames = await ask(agent, "Remember")
    assert not only(frames, ToolFrame)
    assert only(frames, FinalFrame)[0].text.startswith("Tell me what to remember")


@pytest.mark.parametrize("question", [REVENUE_PER_MONTH, "revenue per month in 2024"])
async def test_a_revenue_question_searches_memory_and_quotes_the_match(
    agent: NodestepAgent, question: str
):
    await ask(agent, RULE)
    frames = await ask(agent, question)
    _, _, text = check_answer(frames)
    searched = [
        tool for tool in only(frames, ToolFrame) if tool.name == "search_memory"
    ]
    assert searched[0].arguments == {"query": "revenue"}
    assert searched[1].summary == "1 match: Revenue counts completed orders only"
    assert text.startswith(RULE_NOTE)


async def test_the_category_answer_quotes_the_memory_after_the_clarification(
    agent: NodestepAgent,
):
    await ask(agent, RULE)
    frames = await answer(agent, "gross", thread_id="t-category")
    _, _, text = check_answer(frames, lists_tables=False)
    assert text.startswith(f"{RULE_NOTE}Gross revenue: Coffee ")


async def test_without_a_matching_memory_nothing_is_quoted(agent: NodestepAgent):
    await ask(agent, "Remember that the stores close on Sundays.")
    frames = await ask(agent, REVENUE_PER_MONTH)
    _, _, text = check_answer(frames)
    searched = [
        tool for tool in only(frames, ToolFrame) if tool.name == "search_memory"
    ]
    assert searched[-1].summary == "No match"
    assert "From memory" not in text


async def test_a_memory_edited_by_hand_is_the_one_quoted(
    agent: NodestepAgent, data: Path
):
    await ask(agent, RULE)
    store = MemoryStore(data)
    [entry] = store.entries().memories
    store.edit(
        entry.key,
        MemoryText(title="Revenue", content="Revenue leaves out gift cards."),
    )
    _, _, text = check_answer(await ask(agent, REVENUE_PER_MONTH))
    assert text.startswith("From memory: revenue leaves out gift cards. ")


async def test_forget_searches_memory_and_deletes_the_match(
    agent: NodestepAgent, data: Path
):
    await ask(agent, RULE)
    await ask(agent, "Remember that the stores close on Sundays.")
    frames = await ask(agent, "Forget that revenue counts completed orders only.")
    tools = only(frames, ToolFrame)
    assert [(tool.name, tool.status) for tool in tools] == [
        ("search_memory", "started"),
        ("search_memory", "finished"),
        ("delete_memory", "started"),
        ("delete_memory", "finished"),
    ]
    assert tools[0].arguments == {"query": "revenue counts completed orders only"}
    assert tools[2].arguments == {"key": "revenue-counts-completed-orders-only"}
    [final] = only(frames, FinalFrame)
    assert final.text == "Removed from memory: revenue counts completed orders only."
    [left] = MemoryStore(data).entries().memories
    assert left.content == "The stores close on Sundays."
    _, _, text = check_answer(await ask(agent, REVENUE_PER_MONTH))
    assert "From memory" not in text


@pytest.mark.parametrize(
    "message",
    ["Forget that", "forget it", "Do not remember that.", "Don't remember it"],
)
async def test_forget_that_deletes_the_memory_saved_last_in_the_thread(
    agent: NodestepAgent, data: Path, message: str
):
    await ask(agent, RULE, thread_id="t1")
    frames = await ask(agent, message, thread_id="t1", turn_id="u3")
    tools = only(frames, ToolFrame)
    assert [(tool.name, tool.status) for tool in tools] == [
        ("list_memories", "started"),
        ("list_memories", "finished"),
        ("delete_memory", "started"),
        ("delete_memory", "finished"),
    ]
    assert only(frames, FinalFrame)[0].text == (
        "Removed from memory: revenue counts completed orders only."
    )
    assert MemoryStore(data).entries().memories == []


async def test_forget_it_quotes_the_memory_as_it_is_now(
    agent: NodestepAgent, data: Path
):
    await ask(agent, RULE, thread_id="t1")
    store = MemoryStore(data)
    [entry] = store.entries().memories
    store.edit(
        entry.key,
        MemoryText(title="Revenue", content="Revenue leaves out gift cards."),
    )
    frames = await ask(agent, "Forget it", thread_id="t1", turn_id="u3")
    assert only(frames, FinalFrame)[0].text == (
        "Removed from memory: revenue leaves out gift cards."
    )
    assert store.entries().memories == []


async def test_forget_that_without_a_saved_memory_asks_what_to_forget(
    agent: NodestepAgent,
):
    frames = await ask(agent, "Forget that")
    assert not only(frames, ToolFrame)
    assert only(frames, FinalFrame)[0].text.startswith("Tell me what to forget")


async def test_forgetting_what_is_not_in_memory_says_so(agent: NodestepAgent):
    await ask(agent, "Remember that the year starts in April.")
    frames = await ask(agent, "Forget that the warehouse is in Graz.")
    assert [tool.name for tool in only(frames, ToolFrame)] == [
        "search_memory",
        "search_memory",
    ]
    assert only(frames, FinalFrame)[0].text == (
        'Nothing in memory matches "the warehouse is in Graz".'
    )


async def test_forgetting_a_memory_removed_in_the_meantime_says_so(
    agent: NodestepAgent, data: Path
):
    await ask(agent, RULE, thread_id="t1")
    MemoryStore(data).delete("revenue-counts-completed-orders-only")
    frames = await ask(agent, "forget it", thread_id="t1", turn_id="u3")
    assert [tool.name for tool in only(frames, ToolFrame)] == [
        "list_memories",
        "list_memories",
    ]
    assert only(frames, FinalFrame)[0].text == "That memory was already gone."


async def test_forgetting_something_else_that_shares_a_word_keeps_the_memory(
    agent: NodestepAgent, data: Path
):
    await ask(agent, RULE)
    frames = await ask(agent, "Forget that revenue includes VAT.")
    assert [tool.name for tool in only(frames, ToolFrame)] == [
        "search_memory",
        "search_memory",
    ]
    assert only(frames, FinalFrame)[0].text == (
        'Nothing in memory matches "revenue includes VAT".'
    )
    assert len(MemoryStore(data).entries().memories) == 1


async def test_forget_deletes_a_memory_that_holds_every_word(
    agent: NodestepAgent, data: Path
):
    await ask(agent, RULE)
    frames = await ask(agent, "Forget the completed orders rule for revenue.")
    assert only(frames, FinalFrame)[0].text.startswith("Nothing in memory matches")
    frames = await ask(agent, "Forget that revenue counts completed orders.")
    assert only(frames, FinalFrame)[0].text == (
        "Removed from memory: revenue counts completed orders only."
    )
    assert MemoryStore(data).entries().memories == []


async def test_two_facts_that_start_alike_are_two_memories(
    agent: NodestepAgent, data: Path
):
    start = "Remember that when I say big orders I mean orders "
    await ask(agent, start + "over 500 euros.")
    await ask(agent, start + "with more than 10 items.")
    contents = sorted(entry.content for entry in MemoryStore(data).entries().memories)
    assert contents == [
        "When I say big orders I mean orders over 500 euros.",
        "When I say big orders I mean orders with more than 10 items.",
    ]


@pytest.mark.parametrize("message", ["Remember: !!!", "Remember that ...", "remember"])
async def test_remember_without_words_asks_for_a_fact(
    agent: NodestepAgent, data: Path, message: str
):
    frames = await ask(agent, message)
    assert not only(frames, ToolFrame)
    assert only(frames, FinalFrame)[0].text.startswith("Tell me what to remember")
    assert MemoryStore(data).entries().memories == []


async def test_a_long_word_still_saves_under_a_short_key(
    agent: NodestepAgent, data: Path
):
    frames = await ask(agent, f"Remember that {'q' * 260} breaks and {'a' * 130} b")
    assert [tool.status for tool in only(frames, ToolFrame)] == ["started", "finished"]
    [entry] = MemoryStore(data).entries().memories
    assert len(entry.key) <= KEY_LENGTH
    assert len(entry.title) <= TITLE_LENGTH
    assert entry.content.startswith("Qqq")


async def test_a_fact_too_long_for_a_memory_is_not_saved(
    agent: NodestepAgent, data: Path
):
    frames = await ask(agent, "Remember that " + "word " * 500)
    assert not only(frames, ToolFrame)
    assert only(frames, FinalFrame)[0].text == (
        "That is too long to remember: a memory holds at most 2,000 characters."
    )
    assert MemoryStore(data).entries().memories == []


async def test_the_report_quotes_a_matching_memory(agent: NodestepAgent):
    await ask(agent, RULE)
    _, text = check_report(await ask(agent, REPORT))
    assert text.startswith(f"{RULE_NOTE}Sales by store in 2025")
