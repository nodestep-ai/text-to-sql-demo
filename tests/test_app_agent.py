import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from nodestep import ScriptedChat
from nodestep.chat import AIMessage, ChatRequest, ChatResponse, ToolMessage

from harness import HoldingChat, calls, tool_call
from live import LiveStream
from text_to_sql_demo.app import create_app
from text_to_sql_demo.nodestep_agent import NodestepAgent
from text_to_sql_demo.settings import ServerSettings

PROPOSALS = [
    {"id": "all", "label": "All courses", "description": "Counts every course."},
    {"id": "sql", "label": "SQL courses", "description": "Counts SQL courses."},
]


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


def app_for(settings: ServerSettings, chat: ScriptedChat) -> FastAPI:
    return create_app(
        settings,
        NodestepAgent.from_settings(
            chat, settings, model="gpt-6-luna", provider_name="openai"
        ),
    )


def events(response: Response) -> list[tuple[str, dict]]:
    parsed = []
    for block in response.text.split("\n\n"):
        if block:
            event_line, data_line = block.split("\n")
            parsed.append(
                (
                    event_line.removeprefix("event: "),
                    json.loads(data_line.removeprefix("data: ")),
                )
            )
    return parsed


def new_chat(message: str) -> dict[str, object]:
    return {"thread_id": None, "database": "fixture", "message": message}


def said(request: ChatRequest) -> list[str | None]:
    return [
        message.content
        for message in request.messages
        if message.type == "human"
        or (isinstance(message, AIMessage) and message.content is not None)
    ]


def test_a_question_is_answered_with_sql_and_stored(server_settings: ServerSettings):
    chat = ScriptedChat(
        [
            calls(tool_call("run_sql", {"sql": "SELECT count(*) AS n FROM Teacher"})),
            calls(tool_call("suggest_follow_ups", {"questions": ["A?", "B?"]}, "c2")),
            "There are 3 teachers.",
        ]
    )
    client = TestClient(app_for(server_settings, chat))
    received = events(client.post("/api/chat", json=new_chat("How many teachers?")))
    assert [kind for kind, _ in received] == [
        "run",
        "tool",
        "tool",
        "sql_result",
        "token",
        "token",
        "token",
        "token",
        "final",
        "suggestions",
        "done",
    ]
    thread_id = received[0][1]["thread_id"]
    detail = client.get(f"/api/threads/{thread_id}").json()
    user, assistant = detail["turns"]
    assert user["text"] == "How many teachers?"
    assert assistant["status"] == "completed"
    assert assistant["text"] == "There are 3 teachers."
    assert [frame["type"] for frame in assistant["frames"]] == [
        "tool",
        "tool",
        "sql_result",
        "suggestions",
    ]
    assert assistant["frames"][2]["rows"] == [[3]]


def test_a_clarification_is_answered_through_resume(server_settings: ServerSettings):
    chat = ScriptedChat(
        [
            calls(
                tool_call("ask_user", {"question": "Which?", "proposals": PROPOSALS})
            ),
            "Three courses in all.",
        ]
    )
    client = TestClient(app_for(server_settings, chat))
    received = events(client.post("/api/chat", json=new_chat("How many courses?")))
    assert [kind for kind, _ in received] == ["run", "clarification", "done"]
    thread_id = received[0][1]["thread_id"]
    question = received[1][1]
    assert question["key"].endswith(":clarify")
    assert client.get(f"/api/threads/{thread_id}").json()["turns"][1]["status"] == (
        "waiting"
    )
    answered = events(
        client.post(
            f"/api/threads/{thread_id}/resume",
            json={"key": question["key"], "answer": "all"},
        )
    )
    assert [kind for kind, _ in answered][-2:] == ["final", "done"]
    turns = client.get(f"/api/threads/{thread_id}").json()["turns"]
    assert [turn["status"] for turn in turns] == [
        "completed",
        "completed",
        "completed",
        "completed",
    ]
    reply = chat.requests[1].messages[-1]
    assert isinstance(reply, ToolMessage)
    assert '"label":"All courses"' in (reply.content or "")


def test_an_edit_runs_on_its_own_version(server_settings: ServerSettings):
    chat = ScriptedChat(["A1", "A2", "A1 edited", "A3 on the original", "A3 edited"])
    client = TestClient(app_for(server_settings, chat))
    first = events(client.post("/api/chat", json=new_chat("first")))
    thread_id = first[0][1]["thread_id"]
    client.post("/api/chat", json={"thread_id": thread_id, "message": "second"})
    turn_id = client.get(f"/api/threads/{thread_id}").json()["turns"][0]["id"]
    edited = events(
        client.post(
            f"/api/threads/{thread_id}/turns/{turn_id}/edit",
            json={"message": "first, edited"},
        )
    )
    assert edited[-2][1] == {"text": "A1 edited"}
    assert said(chat.requests[2]) == ["first, edited"]
    shown = client.post(f"/api/threads/{thread_id}/turns/{turn_id}/versions/0")
    assert [turn["text"] for turn in shown.json()["turns"]] == [
        "first",
        "A1",
        "second",
        "A2",
    ]
    client.post("/api/chat", json={"thread_id": thread_id, "message": "third"})
    assert said(chat.requests[3]) == ["first", "A1", "second", "A2", "third"]
    client.post(f"/api/threads/{thread_id}/turns/{turn_id}/versions/1")
    client.post("/api/chat", json={"thread_id": thread_id, "message": "third"})
    assert said(chat.requests[4]) == ["first, edited", "A1 edited", "third"]


async def test_stop_keeps_the_partial_turn_and_the_next_message_works(
    server_settings: ServerSettings,
):
    chat = HoldingChat(
        [
            calls(tool_call("list_tables", {})),
            "Never sent.",
        ],
        hold=2,
    )
    app = app_for(server_settings, chat)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as http,
        LiveStream(app, "/api/chat", new_chat("Tables?")) as stream,
    ):
        _, run = await stream.event()
        kind, data = await stream.event()
        while not (kind == "tool" and data["status"] == "finished"):
            kind, data = await stream.event()
        stop = await http.post(f"/api/threads/{run['thread_id']}/stop")
        assert stop.status_code == 204
        assert await stream.rest() == [("stopped", {}), ("done", {})]
        detail = (await http.get(f"/api/threads/{run['thread_id']}")).json()
        assert detail["turns"][1]["status"] == "stopped"
        again = await http.post(
            "/api/chat",
            json={"thread_id": run["thread_id"], "message": "And now?"},
        )
        assert events(again)[-2][1] == {"text": "Never sent."}
    assert said(chat.requests[2]) == ["Tables?", "And now?"]


def test_text_sent_with_tool_calls_is_replayed_where_it_streamed(
    server_settings: ServerSettings,
):
    chat = ScriptedChat(
        [
            ChatResponse(
                content="Let me list the tables.",
                tool_calls=[tool_call("list_tables", {}, "c1")],
            ),
            "There are three tables.",
        ]
    )
    client = TestClient(app_for(server_settings, chat))
    live = events(client.post("/api/chat", json=new_chat("Tables?")))
    streamed = [kind for kind, _ in live if kind in {"token", "tool", "final"}]
    thread_id = live[0][1]["thread_id"]
    [_, answer] = client.get(f"/api/threads/{thread_id}").json()["turns"]
    assert [frame["type"] for frame in answer["frames"]] == ["token", "tool", "tool"]
    assert answer["frames"][0]["text"] == "Let me list the tables."
    assert answer["text"] == "There are three tables."
    assert streamed.index("tool") > 0
    assert streamed[0] == "token"
