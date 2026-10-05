from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from text_to_sql_demo.agent import ScriptedAgent
from text_to_sql_demo.app import create_app
from text_to_sql_demo.memory import MEMORY_SCOPE, MemoryStore, MemoryText
from text_to_sql_demo.settings import Settings

REVENUE = {"title": "Revenue", "content": "Revenue counts completed orders only."}


@pytest.fixture
def client(settings: Settings, tmp_path: Path) -> TestClient:
    app = create_app(
        settings.model_copy(update={"web_dist": tmp_path / "no-dist"}),
        ScriptedAgent(),
    )
    return TestClient(app)


def memory_folder(settings: Settings) -> Path:
    return settings.memory_folder / "memories" / MEMORY_SCOPE


def test_the_memory_list_starts_empty(client: TestClient):
    response = client.get("/api/memories")
    assert response.status_code == 200
    assert response.json() == {"memories": [], "skipped": []}


def test_a_memory_is_added_listed_edited_and_deleted(
    client: TestClient, settings: Settings
):
    added = client.post("/api/memories", json=REVENUE)
    assert added.status_code == 201
    entry = added.json()
    assert set(entry) == {"key", "title", "content", "created_at", "updated_at"}
    assert (entry["title"], entry["content"]) == (REVENUE["title"], REVENUE["content"])
    assert (memory_folder(settings) / f"{entry['key']}.json").is_file()
    assert client.get("/api/memories").json()["memories"] == [entry]

    edited = client.put(
        f"/api/memories/{entry['key']}",
        json={"title": "Revenue rule", "content": "Refunds count too."},
    )
    assert edited.status_code == 200
    assert edited.json()["key"] == entry["key"]
    assert edited.json()["content"] == "Refunds count too."
    [listed] = client.get("/api/memories").json()["memories"]
    assert listed["title"] == "Revenue rule"

    deleted = client.delete(f"/api/memories/{entry['key']}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/api/memories").json()["memories"] == []


def test_a_memory_the_agent_saved_can_be_edited_by_its_key(
    client: TestClient, settings: Settings
):
    folder = memory_folder(settings)
    folder.mkdir(parents=True)
    store = MemoryStore(settings.memory_folder)
    entry = store.add(MemoryText(title="Stores", content="Riverside opened last."))
    response = client.put(f"/api/memories/{entry.key}", json=REVENUE)
    assert response.status_code == 200
    assert store.entries().memories[0].title == "Revenue"


@pytest.mark.parametrize("key", ["missing", "CON", "a%2Fb", "..%2Fthreads"])
def test_an_unknown_memory_is_404(client: TestClient, key: str):
    client.post("/api/memories", json=REVENUE)
    for response in [
        client.put(f"/api/memories/{key}", json=REVENUE),
        client.delete(f"/api/memories/{key}"),
    ]:
        assert response.status_code == 404
        assert response.json()["code"] == "memory_not_found"
    assert len(client.get("/api/memories").json()["memories"]) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"title": "", "content": "x"},
        {"title": "x", "content": "   "},
        {"title": "x"},
        {"title": "x", "content": "x", "key": "chosen"},
        {"title": "x" * 121, "content": "x"},
    ],
    ids=["empty-title", "blank-content", "no-content", "extra-key", "long-title"],
)
def test_a_bad_memory_body_is_422_and_writes_nothing(
    client: TestClient, settings: Settings, body: dict
):
    assert client.post("/api/memories", json=body).status_code == 422
    entry = client.post("/api/memories", json=REVENUE).json()
    assert client.put(f"/api/memories/{entry['key']}", json=body).status_code == 422
    [listed] = client.get("/api/memories").json()["memories"]
    assert listed == entry


def test_unreadable_memory_files_are_named(client: TestClient, settings: Settings):
    folder = memory_folder(settings)
    folder.mkdir(parents=True)
    (folder / "broken.json").write_bytes(b"\xff")
    assert client.get("/api/memories").json() == {
        "memories": [],
        "skipped": ["broken.json"],
    }


def test_a_memory_file_of_another_scope_is_named_and_cannot_be_changed(
    client: TestClient, settings: Settings
):
    folder = memory_folder(settings)
    folder.mkdir(parents=True)
    (folder / "hand.json").write_text(
        '{"key": "hand", "title": "T", "content": "C", "scope": ".."}',
        encoding="utf-8",
    )
    assert client.get("/api/memories").json() == {
        "memories": [],
        "skipped": ["hand.json"],
    }
    assert client.put("/api/memories/hand", json=REVENUE).status_code == 404
    assert client.delete("/api/memories/hand").status_code == 404
    assert (folder / "hand.json").is_file()
