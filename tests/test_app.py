import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from live import LiveStream
from samples import PROPOSALS
from text_to_sql_demo.agent import (
    AgentCall,
    ChatAgent,
    Delay,
    ScriptedAgent,
    Step,
)
from text_to_sql_demo.app import create_app
from text_to_sql_demo.frames import (
    ClarificationFrame,
    ErrorFrame,
    FinalFrame,
    Frame,
    SqlResultFrame,
    SuggestionsFrame,
    TokenFrame,
    ToolFrame,
    UsageFrame,
)
from text_to_sql_demo.registry import SQLITE_HEADER
from text_to_sql_demo.settings import Settings
from text_to_sql_demo.threads import MAIN_BRANCH

ANSWER: list[Step] = [
    ToolFrame(
        call_id="c1", name="run_sql", status="started", arguments={"sql": "SELECT 1"}
    ),
    ToolFrame(call_id="c1", name="run_sql", status="finished", summary="1 row"),
    SqlResultFrame(
        call_id="c1",
        sql="SELECT count(*) FROM Teacher",
        columns=["count(*)"],
        rows=[[3]],
        row_count=1,
        truncated=False,
        elapsed_ms=1,
    ),
    TokenFrame(text="There are "),
    TokenFrame(text="3 teachers."),
    FinalFrame(text="There are 3 teachers."),
    SuggestionsFrame(questions=["Which teacher has most courses?", "Longest video?"]),
    UsageFrame(model="gpt-6-luna", input_tokens=10, output_tokens=5),
]

QUESTION = ClarificationFrame(
    key="clarify", question="Which revenue?", proposals=PROPOSALS, allow_free_text=True
)
CLARIFY: list[Step] = [QUESTION]
STRICT: list[Step] = [QUESTION.model_copy(update={"allow_free_text": False})]
SLOW: list[Step] = [TokenFrame(text="Part"), Delay(seconds=3600), TokenFrame(text="x")]


def final(text: str) -> list[Step]:
    return [TokenFrame(text=text), FinalFrame(text=text)]


def events(response: Response) -> list[tuple[str, dict]]:
    parsed = []
    for block in response.text.split("\n\n"):
        if not block:
            continue
        event_line, data_line = block.split("\n")
        parsed.append(
            (
                event_line.removeprefix("event: "),
                json.loads(data_line.removeprefix("data: ")),
            )
        )
    return parsed


class ClosingAgent(ScriptedAgent):
    def __init__(self, scripts: list[list[Step]]) -> None:
        super().__init__(scripts)
        self.closed = False

    async def run(
        self, *, thread_id: str, database: str, branch: str, turn_id: str, message: str
    ) -> AsyncGenerator[Frame, None]:
        try:
            async for frame in super().run(
                thread_id=thread_id,
                database=database,
                branch=branch,
                turn_id=turn_id,
                message=message,
            ):
                yield frame
        finally:
            self.closed = True


class SlowCancelAgent(ScriptedAgent):
    def __init__(self, scripts: list[list[Step]]) -> None:
        super().__init__(scripts)
        self.cancelling = asyncio.Event()
        self.release = asyncio.Event()

    async def cancel(self, *, thread_id: str, database: str, branch: str) -> None:
        await super().cancel(thread_id=thread_id, database=database, branch=branch)
        self.cancelling.set()
        await self.release.wait()


def app_for(settings: Settings, agent: ChatAgent, web_dist: Path) -> FastAPI:
    return create_app(settings.model_copy(update={"web_dist": web_dist}), agent)


def client_for(settings: Settings, agent: ChatAgent, web_dist: Path) -> TestClient:
    app = app_for(settings, agent, web_dist)
    assert isinstance(app, FastAPI)
    return TestClient(app)


def new_chat(message: str, database: str = "fixture") -> dict:
    return {"thread_id": None, "database": database, "message": message}


def start_thread(client: TestClient, database: str = "fixture") -> str:
    response = client.post("/api/chat", json=new_chat("q", database))
    return events(response)[0][1]["thread_id"]


def say(client: TestClient, thread_id: str, message: str) -> list[tuple[str, dict]]:
    response = client.post(
        "/api/chat", json={"thread_id": thread_id, "message": message}
    )
    assert response.status_code == 200
    return events(response)


def turns(client: TestClient, thread_id: str) -> list[dict]:
    return client.get(f"/api/threads/{thread_id}").json()["turns"]


@pytest.fixture
def no_dist(tmp_path: Path) -> Path:
    return tmp_path / "no-dist"


@pytest.fixture
def shop(databases_dir: Path, build_database: Callable[[Path], Path]) -> Path:
    path = build_database(databases_dir / "shop.sqlite")
    (databases_dir / "shop.json").write_text(
        '{"title": "Shop", "description": "A second database.", '
        '"examples": ["Who teaches the most courses?"]}',
        encoding="utf-8",
    )
    return path


def test_databases_route_lists_the_folder(
    settings: Settings, no_dist: Path, database_path: Path, shop: Path
):
    client = client_for(settings, ScriptedAgent(), no_dist)
    response = client.get("/api/databases")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "fixture",
            "title": "fixture",
            "description": "",
            "table_count": 3,
            "size_bytes": database_path.stat().st_size,
            "examples": [],
        },
        {
            "id": "shop",
            "title": "Shop",
            "description": "A second database.",
            "table_count": 3,
            "size_bytes": shop.stat().st_size,
            "examples": ["Who teaches the most courses?"],
        },
    ]


def test_a_database_copied_in_shows_up_without_a_restart(
    settings: Settings,
    no_dist: Path,
    databases_dir: Path,
    build_database: Callable[[Path], Path],
):
    agent = ScriptedAgent([ANSWER])
    client = client_for(settings, agent, no_dist)
    assert [database["id"] for database in client.get("/api/databases").json()] == [
        "fixture"
    ]
    build_database(databases_dir / "late.sqlite")
    assert [database["id"] for database in client.get("/api/databases").json()] == [
        "fixture",
        "late",
    ]
    assert client.get("/api/databases/late/schema").status_code == 200
    response = client.post("/api/chat", json=new_chat("q", "late"))
    assert events(response)[0][1]["database"] == "late"


def test_without_a_databases_folder_the_list_is_empty(
    settings: Settings, tmp_path: Path
):
    empty = settings.model_copy(update={"data_dir": tmp_path / "empty"})
    client = client_for(empty, ScriptedAgent(), tmp_path / "no-dist")
    assert client.get("/api/databases").json() == []


def test_schema_route_returns_the_contract_shape(settings: Settings, no_dist: Path):
    client = client_for(settings, ScriptedAgent(), no_dist)
    response = client.get("/api/databases/fixture/schema")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"database", "tables"}
    assert body["database"] == "fixture"
    assert [table["name"] for table in body["tables"]] == ["Course", "Teacher", "Video"]
    course = body["tables"][0]
    assert set(course) == {"name", "row_count", "columns"}
    assert course["columns"][2] == {
        "name": "TeacherId",
        "type": "INTEGER",
        "primary_key": False,
        "references": {"table": "Teacher", "column": "TeacherId"},
    }


@pytest.mark.parametrize("database_id", ["nope", "FIXTURE", "fixture.sqlite", "a.b"])
def test_unknown_database_schema_is_404(
    settings: Settings, no_dist: Path, databases_dir: Path, database_id: str
):
    client = client_for(settings, ScriptedAgent(), no_dist)
    response = client.get(f"/api/databases/{database_id}/schema")
    assert response.status_code == 404
    assert database_id in response.json()["detail"]
    assert str(databases_dir) not in response.json()["detail"]


def test_a_broken_database_file_is_left_out(
    settings: Settings, no_dist: Path, databases_dir: Path
):
    (databases_dir / "broken.sqlite").write_bytes(SQLITE_HEADER)
    agent = ScriptedAgent([ANSWER])
    client = client_for(settings, agent, no_dist)
    listed = client.get("/api/databases")
    assert listed.status_code == 200
    assert [database["id"] for database in listed.json()] == ["fixture"]
    schema = client.get("/api/databases/broken/schema")
    chat = client.post("/api/chat", json=new_chat("q", "broken"))
    for response in (schema, chat):
        assert response.status_code == 404
        assert "broken" in response.json()["detail"]
    assert agent.calls == []


def test_a_damaged_database_schema_is_500_with_a_reason(
    settings: Settings,
    no_dist: Path,
    databases_dir: Path,
    build_damaged_database: Callable[[Path], Path],
):
    build_damaged_database(databases_dir / "damaged.sqlite")
    client = client_for(settings, ScriptedAgent(), no_dist)
    assert [database["id"] for database in client.get("/api/databases").json()] == [
        "damaged",
        "fixture",
    ]
    response = client.get("/api/databases/damaged/schema")
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Database 'damaged' could not be read: database disk image is malformed.",
        "code": "database_unreadable",
    }


def test_the_single_schema_route_is_gone(settings: Settings, no_dist: Path):
    client = client_for(settings, ScriptedAgent(), no_dist)
    assert client.get("/api/schema").status_code == 404


def test_chat_streams_run_frames_done(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([ANSWER])
    client = client_for(settings, agent, no_dist)
    response = client.post("/api/chat", json=new_chat("How many teachers?"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    received = events(response)
    kind, run = received[0]
    assert kind == "run"
    assert set(run) == {"thread_id", "run_id", "database"}
    assert run["database"] == "fixture"
    assert [kind for kind, _ in received[1:]] == [
        "tool",
        "tool",
        "sql_result",
        "token",
        "token",
        "final",
        "suggestions",
        "usage",
        "done",
    ]
    assert received[1][1] == {
        "call_id": "c1",
        "name": "run_sql",
        "status": "started",
        "arguments": {"sql": "SELECT 1"},
        "summary": None,
    }
    assert received[7][1] == {
        "questions": ["Which teacher has most courses?", "Longest video?"]
    }
    assert received[-1] == ("done", {})
    user = turns(client, run["thread_id"])[0]
    assert agent.calls == [
        AgentCall(
            method="run",
            thread_id=run["thread_id"],
            database="fixture",
            branch=MAIN_BRANCH,
            turn_id=user["id"],
            message="How many teachers?",
        )
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"thread_id": None, "message": "hi"},
        {"thread_id": None, "database": None, "message": "hi"},
    ],
    ids=["absent", "null"],
)
def test_new_thread_without_a_database_is_422(
    settings: Settings, no_dist: Path, body: dict
):
    agent = ScriptedAgent([ANSWER])
    client = client_for(settings, agent, no_dist)
    response = client.post("/api/chat", json=body)
    assert response.status_code == 422
    assert "database" in json.dumps(response.json()["detail"])
    assert agent.calls == []
    assert not (settings.data_dir / "threads.json").exists()


@pytest.mark.parametrize("database_id", ["nope", "../databases/fixture", "FIXTURE"])
def test_new_thread_with_an_unknown_database_is_404(
    settings: Settings, no_dist: Path, database_id: str
):
    agent = ScriptedAgent([ANSWER])
    client = client_for(settings, agent, no_dist)
    response = client.post("/api/chat", json=new_chat("hi", database_id))
    assert response.status_code == 404
    assert database_id in response.json()["detail"]
    assert str(settings.databases_dir) not in response.json()["detail"]
    assert agent.calls == []
    assert not (settings.data_dir / "threads.json").exists()


def test_thread_is_listed_and_replayed(settings: Settings, no_dist: Path):
    client = client_for(settings, ScriptedAgent([ANSWER]), no_dist)
    response = client.post("/api/chat", json=new_chat("How many teachers?"))
    thread_id = events(response)[0][1]["thread_id"]
    [summary] = client.get("/api/threads").json()
    assert set(summary) == {"id", "title", "database", "updated_at"}
    assert summary["id"] == thread_id
    assert summary["title"] == "How many teachers?"
    assert summary["database"] == "fixture"
    detail = client.get(f"/api/threads/{thread_id}").json()
    assert set(detail) == {"id", "title", "database", "turns"}
    assert detail["database"] == "fixture"
    user, assistant = detail["turns"]
    assert set(user) == {"id", "role", "text", "frames", "status", "version", "answers"}
    assert user == {
        "id": user["id"],
        "role": "user",
        "text": "How many teachers?",
        "frames": [],
        "status": "completed",
        "version": None,
        "answers": None,
    }
    assert set(assistant) == set(user)
    assert assistant["id"] != user["id"]
    assert assistant["role"] == "assistant"
    assert assistant["status"] == "completed"
    assert assistant["version"] is None
    assert assistant["answers"] is None
    assert assistant["text"] == "There are 3 teachers."
    assert [frame["type"] for frame in assistant["frames"]] == [
        "tool",
        "tool",
        "sql_result",
        "suggestions",
        "usage",
    ]
    assert assistant["frames"][2]["rows"] == [[3]]


def test_second_chat_continues_the_thread(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([ANSWER, ANSWER, ANSWER])
    client = client_for(settings, agent, no_dist)
    first = client.post("/api/chat", json=new_chat("one"))
    thread_id = events(first)[0][1]["thread_id"]
    second = client.post(
        "/api/chat",
        json={"thread_id": thread_id, "database": "fixture", "message": "two"},
    )
    third = client.post("/api/chat", json={"thread_id": thread_id, "message": "three"})
    for response in (second, third):
        assert events(response)[0][1] == {
            "thread_id": thread_id,
            "run_id": events(response)[0][1]["run_id"],
            "database": "fixture",
        }
    shown = turns(client, thread_id)
    assert [(call.thread_id, call.database, call.branch) for call in agent.calls] == [
        (thread_id, "fixture", MAIN_BRANCH)
    ] * 3
    assert [call.turn_id for call in agent.calls] == [
        turn["id"] for turn in shown if turn["role"] == "user"
    ]
    assert len(client.get("/api/threads").json()) == 1
    assert len(shown) == 6


@pytest.mark.parametrize("other", ["shop", "nope"])
def test_another_database_on_an_existing_thread_is_409(
    settings: Settings, no_dist: Path, shop: Path, other: str
):
    agent = ScriptedAgent([ANSWER, ANSWER])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    response = client.post(
        "/api/chat", json={"thread_id": thread_id, "database": other, "message": "q"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "database_mismatch"
    detail = response.json()["detail"]
    assert "fixture" in detail
    assert other in detail
    assert len(agent.calls) == 1
    assert len(turns(client, thread_id)) == 2


def test_threads_on_different_databases(settings: Settings, no_dist: Path, shop: Path):
    agent = ScriptedAgent([ANSWER, ANSWER])
    client = client_for(settings, agent, no_dist)
    first = start_thread(client, "fixture")
    second = start_thread(client, "shop")
    listed = {
        thread["id"]: thread["database"] for thread in client.get("/api/threads").json()
    }
    assert listed == {first: "fixture", second: "shop"}
    assert [call.database for call in agent.calls] == ["fixture", "shop"]


def test_a_thread_whose_database_was_removed_is_404(
    settings: Settings, no_dist: Path, shop: Path
):
    agent = ScriptedAgent([CLARIFY, ANSWER, ANSWER])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client, "shop")
    first = turns(client, thread_id)[0]
    shop.unlink()
    chat = client.post("/api/chat", json={"thread_id": thread_id, "message": "q"})
    resume = client.post(
        f"/api/threads/{thread_id}/resume", json={"key": "clarify", "answer": "net"}
    )
    edit = client.post(
        f"/api/threads/{thread_id}/turns/{first['id']}/edit", json={"message": "x"}
    )
    for response in (chat, resume, edit):
        assert response.status_code == 404
        assert "shop" in response.json()["detail"]
    assert len(agent.calls) == 1
    assert len(turns(client, thread_id)) == 2


def test_unknown_thread_is_404(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([ANSWER])
    client = client_for(settings, agent, no_dist)
    responses = [
        client.post(
            "/api/chat",
            json={"thread_id": "nope", "database": "fixture", "message": "hi"},
        ),
        client.post("/api/threads/nope/resume", json={"key": "k", "answer": "a"}),
        client.get("/api/threads/nope"),
        client.post("/api/threads/nope/turns/t/edit", json={"message": "x"}),
        client.post("/api/threads/nope/turns/t/versions/0"),
        client.post("/api/threads/nope/stop"),
    ]
    for response in responses:
        assert response.status_code == 404
        assert set(response.json()) == {"detail", "code"}
        assert response.json()["code"] == "thread_not_found"
        assert "nope" in response.json()["detail"]
    assert agent.calls == []
    assert not (settings.data_dir / "threads.json").exists()


@pytest.mark.parametrize(
    "body",
    [
        {"thread_id": None, "database": "fixture", "message": "   "},
        {"database": "fixture", "message": "hi"},
        {"thread_id": None, "database": "fixture", "message": "hi", "extra": 1},
        {"thread_id": None, "database": "  ", "message": "hi"},
    ],
    ids=["blank-message", "no-thread-id", "extra-key", "blank-database"],
)
def test_bad_chat_body_is_422(settings: Settings, no_dist: Path, body: dict):
    agent = ScriptedAgent([ANSWER])
    client = client_for(settings, agent, no_dist)
    assert client.post("/api/chat", json=body).status_code == 422
    assert agent.calls == []


def test_bad_resume_body_is_422(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([CLARIFY])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    response = client.post(
        f"/api/threads/{thread_id}/resume", json={"key": "clarify", "answer": ""}
    )
    assert response.status_code == 422


def test_resume_with_a_proposal(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([CLARIFY, final("Net revenue was 3.")])
    client = client_for(settings, agent, no_dist)
    first = events(client.post("/api/chat", json=new_chat("Revenue?")))
    thread_id = first[0][1]["thread_id"]
    assert [kind for kind, _ in first] == ["run", "clarification", "done"]
    assert first[1][1] == {
        "key": "clarify",
        "question": "Which revenue?",
        "proposals": [proposal.model_dump() for proposal in PROPOSALS],
        "allow_free_text": True,
    }
    assert [turn["status"] for turn in turns(client, thread_id)] == [
        "completed",
        "waiting",
    ]
    resumed = events(
        client.post(
            f"/api/threads/{thread_id}/resume",
            json={"key": "clarify", "answer": "net"},
        )
    )
    assert [kind for kind, _ in resumed] == ["run", "token", "final", "done"]
    assert resumed[0][1]["thread_id"] == thread_id
    assert resumed[0][1]["database"] == "fixture"
    assert resumed[0][1]["run_id"] != first[0][1]["run_id"]
    shown = turns(client, thread_id)
    assert agent.calls[1] == AgentCall(
        method="resume",
        thread_id=thread_id,
        database="fixture",
        branch=MAIN_BRANCH,
        turn_id=shown[2]["id"],
        key="clarify",
        answer="net",
    )
    assert [(turn["role"], turn["text"], turn["status"]) for turn in shown] == [
        ("user", "Revenue?", "completed"),
        ("assistant", "", "completed"),
        ("user", "net", "completed"),
        ("assistant", "Net revenue was 3.", "completed"),
    ]
    assert [turn["answers"] for turn in shown] == [None, None, "clarify", None]
    assert shown[1]["frames"][0]["type"] == "clarification"


def test_resume_with_free_text(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([CLARIFY, final("Ok.")])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    response = client.post(
        f"/api/threads/{thread_id}/resume",
        json={"key": "clarify", "answer": "only 2025 orders"},
    )
    assert response.status_code == 200
    assert agent.calls[1].answer == "only 2025 orders"


def test_free_text_is_refused_when_the_question_does_not_allow_it(
    settings: Settings, no_dist: Path
):
    agent = ScriptedAgent([STRICT, final("Ok.")])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    response = client.post(
        f"/api/threads/{thread_id}/resume",
        json={"key": "clarify", "answer": "something else"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "gross" in detail
    assert "net" in detail
    assert len(agent.calls) == 1
    assert [turn["status"] for turn in turns(client, thread_id)] == [
        "completed",
        "waiting",
    ]
    accepted = client.post(
        f"/api/threads/{thread_id}/resume", json={"key": "clarify", "answer": "gross"}
    )
    assert accepted.status_code == 200


@pytest.mark.parametrize(
    ("script", "key"),
    [(ANSWER, "clarify"), (CLARIFY, "made-up")],
    ids=["no-question", "other-key"],
)
def test_resume_without_that_open_question_is_409(
    settings: Settings, no_dist: Path, script: list[Step], key: str
):
    agent = ScriptedAgent([script, final("x")])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    response = client.post(
        f"/api/threads/{thread_id}/resume", json={"key": key, "answer": "net"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "clarification_not_pending"
    assert key in response.json()["detail"]
    assert [call.method for call in agent.calls] == ["run"]
    assert len(turns(client, thread_id)) == 2


def test_an_answered_question_cannot_be_answered_again(
    settings: Settings, no_dist: Path
):
    agent = ScriptedAgent([CLARIFY, final("Done."), final("x")])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    body = {"key": "clarify", "answer": "gross"}
    first = client.post(f"/api/threads/{thread_id}/resume", json=body)
    assert first.status_code == 200
    second = client.post(f"/api/threads/{thread_id}/resume", json=body)
    assert second.status_code == 409
    assert len(agent.calls) == 2


def test_a_later_question_can_be_answered(settings: Settings, no_dist: Path):
    later = QUESTION.model_copy(update={"key": "period"})
    usage = UsageFrame(model="gpt-6-luna", input_tokens=1, output_tokens=1)
    agent = ScriptedAgent([CLARIFY, [later, usage], final("Gross.")])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    client.post(
        f"/api/threads/{thread_id}/resume", json={"key": "clarify", "answer": "gross"}
    )
    response = client.post(
        f"/api/threads/{thread_id}/resume", json={"key": "period", "answer": "net"}
    )
    assert response.status_code == 200
    assert agent.calls[2].key == "period"


def test_a_waiting_thread_takes_no_new_message(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([CLARIFY, ANSWER])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    response = client.post(
        "/api/chat", json={"thread_id": thread_id, "message": "something else"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "clarification_pending"
    assert "clarify" in response.json()["detail"]
    assert len(agent.calls) == 1
    assert len(turns(client, thread_id)) == 2


def test_agent_exception_becomes_error_then_done(settings: Settings, no_dist: Path):
    agent = ScriptedAgent(
        [[TokenFrame(text="Partial"), RuntimeError("model unavailable")]]
    )
    client = client_for(settings, agent, no_dist)
    received = events(client.post("/api/chat", json=new_chat("q")))
    assert [kind for kind, _ in received] == ["run", "token", "error", "done"]
    assert received[2][1] == {"kind": "agent_error", "message": "model unavailable"}
    thread_id = received[0][1]["thread_id"]
    assistant = turns(client, thread_id)[1]
    assert assistant["text"] == "Partial"
    assert assistant["status"] == "error"
    assert assistant["frames"] == [
        {"type": "error", "kind": "agent_error", "message": "model unavailable"}
    ]


def test_an_error_frame_stores_an_error_turn(settings: Settings, no_dist: Path):
    failure = ErrorFrame(kind="agent_unavailable", message="No agent.")
    client = client_for(settings, ScriptedAgent([[failure]]), no_dist)
    received = events(client.post("/api/chat", json=new_chat("q")))
    assert [kind for kind, _ in received] == ["run", "error", "done"]
    assistant = turns(client, received[0][1]["thread_id"])[1]
    assert assistant["status"] == "error"
    assert assistant["frames"][0]["kind"] == "agent_unavailable"


def test_serves_the_built_web_app(settings: Settings, tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><title>text-to-sql-demo</title>", encoding="utf-8"
    )
    client = client_for(settings, ScriptedAgent(), dist)
    page = client.get("/")
    assert page.status_code == 200
    assert "<title>text-to-sql-demo</title>" in page.text
    assert client.get("/api/databases").headers["content-type"] == "application/json"


def test_without_a_build_the_root_is_404(settings: Settings, no_dist: Path):
    assert client_for(settings, ScriptedAgent(), no_dist).get("/").status_code == 404


def test_editing_the_first_of_three_turns_and_switching_versions(
    settings: Settings, no_dist: Path
):
    agent = ScriptedAgent(
        [
            final("a1"),
            final("a2"),
            final("a3"),
            final("a1 edited"),
            final("a4"),
            final("a5"),
        ]
    )
    client = client_for(settings, agent, no_dist)
    thread_id = events(client.post("/api/chat", json=new_chat("q1")))[0][1]["thread_id"]
    say(client, thread_id, "q2")
    say(client, thread_id, "q3")
    original = turns(client, thread_id)
    first = original[0]

    response = client.post(
        f"/api/threads/{thread_id}/turns/{first['id']}/edit",
        json={"message": "q1 edited"},
    )
    assert response.status_code == 200
    received = events(response)
    assert [kind for kind, _ in received] == ["run", "token", "final", "done"]
    assert received[0][1]["thread_id"] == thread_id
    edit = agent.calls[3]
    assert edit.method == "edit"
    assert (edit.turn_id, edit.message) == (first["id"], "q1 edited")
    assert edit.branch != MAIN_BRANCH
    edited = turns(client, thread_id)
    assert [(turn["role"], turn["text"]) for turn in edited] == [
        ("user", "q1 edited"),
        ("assistant", "a1 edited"),
    ]
    assert edited[0]["id"] == first["id"]
    assert edited[0]["version"] == {"index": 1, "count": 2}

    back = client.post(f"/api/threads/{thread_id}/turns/{first['id']}/versions/0")
    assert back.status_code == 200
    assert [turn["id"] for turn in back.json()["turns"]] == [t["id"] for t in original]
    assert [turn["text"] for turn in back.json()["turns"]] == [
        "q1",
        "a1",
        "q2",
        "a2",
        "q3",
        "a3",
    ]
    assert back.json()["turns"][0]["version"] == {"index": 0, "count": 2}
    assert turns(client, thread_id) == back.json()["turns"]

    say(client, thread_id, "q4")
    assert agent.calls[4].branch == MAIN_BRANCH
    assert [turn["text"] for turn in turns(client, thread_id)][-2:] == ["q4", "a4"]

    forth = client.post(f"/api/threads/{thread_id}/turns/{first['id']}/versions/1")
    assert [turn["text"] for turn in forth.json()["turns"]] == [
        "q1 edited",
        "a1 edited",
    ]
    say(client, thread_id, "q5")
    assert agent.calls[5].branch == edit.branch
    assert [turn["text"] for turn in turns(client, thread_id)] == [
        "q1 edited",
        "a1 edited",
        "q5",
        "a5",
    ]

    again = client.post(f"/api/threads/{thread_id}/turns/{first['id']}/versions/0")
    assert [turn["text"] for turn in again.json()["turns"]] == [
        "q1",
        "a1",
        "q2",
        "a2",
        "q3",
        "a3",
        "q4",
        "a4",
    ]


def test_edits_that_are_refused(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([CLARIFY, final("a")])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    client.post(
        f"/api/threads/{thread_id}/resume", json={"key": "clarify", "answer": "net"}
    )
    user, assistant, answer, _ = turns(client, thread_id)
    base = f"/api/threads/{thread_id}/turns"
    cases = {
        (f"{base}/{assistant['id']}/edit", "x"): 422,
        (f"{base}/{answer['id']}/edit", "x"): 422,
        (f"{base}/{assistant['id']}/versions/0", None): 422,
        (f"{base}/nope/edit", "x"): 404,
        (f"{base}/nope/versions/0", None): 404,
        (f"{base}/{user['id']}/versions/1", None): 404,
        (f"{base}/{user['id']}/versions/-1", None): 404,
        (f"{base}/{user['id']}/edit", "   "): 422,
    }
    for (url, message), status in cases.items():
        body = None if message is None else {"message": message}
        response = client.post(url, json=body)
        assert response.status_code == status, url
    assert len(agent.calls) == 2
    assert len(turns(client, thread_id)) == 4


def test_a_waiting_thread_can_be_edited(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([CLARIFY, final("a")])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    first = turns(client, thread_id)[0]
    response = client.post(
        f"/api/threads/{thread_id}/turns/{first['id']}/edit",
        json={"message": "gross revenue per month"},
    )
    assert response.status_code == 200
    assert [(t["text"], t["status"]) for t in turns(client, thread_id)] == [
        ("gross revenue per month", "completed"),
        ("a", "completed"),
    ]


def test_stop_without_a_run_is_204(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([ANSWER])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    before = turns(client, thread_id)
    for _ in range(2):
        response = client.post(f"/api/threads/{thread_id}/stop")
        assert response.status_code == 204
        assert response.content == b""
    assert turns(client, thread_id) == before
    assert [call.method for call in agent.calls] == ["run"]


def test_stop_during_a_clarification_wait(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([CLARIFY, ANSWER])
    client = client_for(settings, agent, no_dist)
    thread_id = start_thread(client)
    assert client.post(f"/api/threads/{thread_id}/stop").status_code == 204
    assert [turn["status"] for turn in turns(client, thread_id)] == [
        "completed",
        "stopped",
    ]
    assert agent.calls[1] == AgentCall(
        method="cancel", thread_id=thread_id, database="fixture", branch=MAIN_BRANCH
    )
    resume = client.post(
        f"/api/threads/{thread_id}/resume", json={"key": "clarify", "answer": "net"}
    )
    assert resume.status_code == 409
    assert client.post(f"/api/threads/{thread_id}/stop").status_code == 204
    assert len(agent.calls) == 2
    received = say(client, thread_id, "How many teachers?")
    assert received[-1] == ("done", {})
    assert agent.calls[2].method == "run"
    assert [turn["status"] for turn in turns(client, thread_id)] == [
        "completed",
        "stopped",
        "completed",
        "completed",
    ]


def async_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_stop_during_a_slow_stream(settings: Settings, no_dist: Path):
    agent = ScriptedAgent([SLOW, final("fresh")])
    app = app_for(settings, agent, no_dist)
    async with (
        async_client(app) as http,
        LiveStream(app, "/api/chat", new_chat("Write a long report")) as stream,
    ):
        assert stream.status == 200
        kind, run = await stream.event()
        assert kind == "run"
        assert await stream.event() == ("token", {"text": "Part"})
        thread_id = run["thread_id"]
        stop = await http.post(f"/api/threads/{thread_id}/stop")
        assert stop.status_code == 204
        assert await stream.rest() == [("stopped", {}), ("done", {})]
        detail = (await http.get(f"/api/threads/{thread_id}")).json()
        assert [(t["role"], t["text"], t["status"]) for t in detail["turns"]] == [
            ("user", "Write a long report", "completed"),
            ("assistant", "Part", "stopped"),
        ]
        assert detail["turns"][1]["frames"] == []
        assert agent.calls[1] == AgentCall(
            method="cancel",
            thread_id=thread_id,
            database="fixture",
            branch=MAIN_BRANCH,
        )
        again = await http.post(f"/api/threads/{thread_id}/stop")
        assert again.status_code == 204
        assert len(agent.calls) == 2
        fresh = await http.post(
            "/api/chat", json={"thread_id": thread_id, "message": "Shorter, please"}
        )
        assert fresh.status_code == 200
        assert [kind for kind, _ in events(fresh)][-2:] == ["final", "done"]
        detail = (await http.get(f"/api/threads/{thread_id}")).json()
        assert [(t["text"], t["status"]) for t in detail["turns"]][2:] == [
            ("Shorter, please", "completed"),
            ("fresh", "completed"),
        ]


async def test_requests_while_a_run_is_active_are_409(
    settings: Settings, no_dist: Path
):
    agent = ScriptedAgent([CLARIFY, SLOW])
    app = app_for(settings, agent, no_dist)
    async with async_client(app) as http:
        started = await http.post("/api/chat", json=new_chat("Revenue?"))
        thread_id = events(started)[0][1]["thread_id"]
        detail = (await http.get(f"/api/threads/{thread_id}")).json()
        first = detail["turns"][0]
        async with LiveStream(
            app,
            f"/api/threads/{thread_id}/resume",
            {"key": "clarify", "answer": "net"},
        ) as stream:
            await stream.event()
            await stream.event()
            during = (await http.get(f"/api/threads/{thread_id}")).json()["turns"]
            responses = [
                await http.post(
                    "/api/chat", json={"thread_id": thread_id, "message": "q"}
                ),
                await http.post(
                    f"/api/threads/{thread_id}/turns/{first['id']}/edit",
                    json={"message": "x"},
                ),
                await http.post(
                    f"/api/threads/{thread_id}/resume",
                    json={"key": "clarify", "answer": "gross"},
                ),
            ]
            for response in responses:
                assert response.status_code == 409
                assert response.json()["code"] == "run_active"
                assert "still answering" in response.json()["detail"]
            assert (await http.get(f"/api/threads/{thread_id}")).json()[
                "turns"
            ] == during
            assert [call.method for call in agent.calls] == ["run", "resume"]
            assert (
                await http.post(f"/api/threads/{thread_id}/stop")
            ).status_code == 204
            assert await stream.rest() == [("stopped", {}), ("done", {})]


async def test_a_disconnect_in_mid_stream_stops_the_run(
    settings: Settings, no_dist: Path
):
    agent = ClosingAgent([SLOW])
    app = app_for(settings, agent, no_dist)
    stream = LiveStream(app, "/api/chat", new_chat("Write a long report"))
    async with async_client(app) as http, stream:
        _, run = await stream.event()
        assert await stream.event() == ("token", {"text": "Part"})
        await stream.disconnect()
        assert agent.closed is True
        thread_id = run["thread_id"]
        detail = (await http.get(f"/api/threads/{thread_id}")).json()
        assert [(t["text"], t["status"]) for t in detail["turns"]] == [
            ("Write a long report", "completed"),
            ("Part", "stopped"),
        ]
        assert [call.method for call in agent.calls] == ["run", "cancel"]
        assert (await http.post(f"/api/threads/{thread_id}/stop")).status_code == 204
        assert len(agent.calls) == 2


async def test_a_version_switch_while_a_run_is_active_is_409(
    settings: Settings, no_dist: Path
):
    agent = ScriptedAgent([final("a1"), SLOW])
    app = app_for(settings, agent, no_dist)
    async with async_client(app) as http:
        started = await http.post("/api/chat", json=new_chat("q1"))
        thread_id = events(started)[0][1]["thread_id"]
        first = (await http.get(f"/api/threads/{thread_id}")).json()["turns"][0]
        switch = f"/api/threads/{thread_id}/turns/{first['id']}/versions/0"
        async with LiveStream(
            app,
            f"/api/threads/{thread_id}/turns/{first['id']}/edit",
            {"message": "q1 edited"},
        ) as stream:
            await stream.event()
            assert await stream.event() == ("token", {"text": "Part"})
            response = await http.post(switch)
            assert response.status_code == 409
            assert response.json()["code"] == "run_active"
            assert (
                await http.post(f"/api/threads/{thread_id}/stop")
            ).status_code == 204
            assert await stream.rest() == [("stopped", {}), ("done", {})]
        detail = (await http.get(f"/api/threads/{thread_id}")).json()
        assert [(t["text"], t["status"]) for t in detail["turns"]] == [
            ("q1 edited", "completed"),
            ("Part", "stopped"),
        ]
        back = await http.post(switch)
        assert back.status_code == 200
        assert [t["text"] for t in back.json()["turns"]] == ["q1", "a1"]


async def test_a_thread_stays_busy_while_its_question_is_closed(
    settings: Settings, no_dist: Path
):
    agent = SlowCancelAgent([CLARIFY, ANSWER])
    app = app_for(settings, agent, no_dist)
    async with async_client(app) as http:
        started = await http.post("/api/chat", json=new_chat("Revenue?"))
        thread_id = events(started)[0][1]["thread_id"]
        stop = asyncio.create_task(http.post(f"/api/threads/{thread_id}/stop"))
        await asyncio.wait_for(agent.cancelling.wait(), 5)
        body = {"thread_id": thread_id, "message": "q"}
        during = await http.post("/api/chat", json=body)
        assert during.status_code == 409
        assert during.json()["code"] == "run_active"
        again = await http.post(f"/api/threads/{thread_id}/stop")
        assert again.status_code == 204
        agent.release.set()
        assert (await stop).status_code == 204
        after = await http.post("/api/chat", json=body)
        assert after.status_code == 200
        assert [call.method for call in agent.calls] == ["run", "cancel", "run"]
