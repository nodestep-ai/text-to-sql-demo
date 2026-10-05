import time
from pathlib import Path

import pytest
from nodestep import ToolContext, call_tool
from nodestep.middleware import Memory, MemorySearchResult
from pydantic import ValidationError

from text_to_sql_demo.errors import MemoryNotFoundError
from text_to_sql_demo.memory import (
    CONTENT_LENGTH,
    KEY_LENGTH,
    MEMORY_SCOPE,
    TITLE_LENGTH,
    MemoryStore,
    MemoryText,
)

REVENUE = MemoryText(title="Revenue", content="Revenue counts completed orders only.")


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "data")


def memory_files(store: MemoryStore) -> list[str]:
    folder = store.folder / "memories" / MEMORY_SCOPE
    return sorted(path.name for path in folder.glob("*.json"))


async def agent_tool(store: MemoryStore, name: str, arguments: dict) -> object:
    tools = {tool.name: tool for tool in store.tools()}
    return (await call_tool(tools[name], arguments, ToolContext())).value


def test_an_empty_memory_lists_nothing(store: MemoryStore):
    listing = store.entries()
    assert listing.memories == []
    assert listing.skipped == []


def test_a_memory_added_by_hand_is_a_file_the_agent_finds(store: MemoryStore):
    entry = store.add(REVENUE)
    assert (entry.title, entry.content) == (REVENUE.title, REVENUE.content)
    assert memory_files(store) == [f"{entry.key}.json"]
    assert [memory.key for memory in store.entries().memories] == [entry.key]


async def test_the_agent_searches_what_was_added_by_hand(store: MemoryStore):
    entry = store.add(REVENUE)
    found = await agent_tool(store, "search_memory", {"query": "completed orders"})
    assert isinstance(found, MemorySearchResult)
    assert [memory.key for memory in found.results] == [entry.key]


async def test_what_the_agent_saves_is_listed(store: MemoryStore):
    await agent_tool(
        store,
        "save_memory",
        {"key": "fiscal-year", "title": "Fiscal year", "content": "Starts in April."},
    )
    [entry] = store.entries().memories
    assert (entry.key, entry.title, entry.content) == (
        "fiscal-year",
        "Fiscal year",
        "Starts in April.",
    )


def test_the_latest_change_comes_first(store: MemoryStore):
    first = store.add(REVENUE)
    time.sleep(0.01)
    second = store.add(MemoryText(title="Stores", content="Riverside opened last."))
    assert [memory.key for memory in store.entries().memories] == [
        second.key,
        first.key,
    ]
    time.sleep(0.01)
    store.edit(first.key, REVENUE)
    assert [memory.key for memory in store.entries().memories] == [
        first.key,
        second.key,
    ]


def test_an_edit_keeps_the_key_and_the_creation_time(store: MemoryStore):
    entry = store.add(REVENUE)
    time.sleep(0.01)
    changed = store.edit(
        entry.key, MemoryText(title="Revenue rule", content="Count refunds too.")
    )
    assert changed.key == entry.key
    assert (changed.title, changed.content) == ("Revenue rule", "Count refunds too.")
    assert changed.created_at == entry.created_at
    assert changed.updated_at is not None
    assert entry.updated_at is not None
    assert changed.updated_at > entry.updated_at
    assert memory_files(store) == [f"{entry.key}.json"]


def test_a_delete_removes_the_file(store: MemoryStore):
    entry = store.add(REVENUE)
    store.delete(entry.key)
    assert store.entries().memories == []
    assert memory_files(store) == []


@pytest.mark.parametrize("key", ["missing", "CON", "dot.", "../escape", "a/b", ""])
def test_an_unknown_key_cannot_be_edited_or_deleted(store: MemoryStore, key: str):
    store.add(REVENUE)
    with pytest.raises(MemoryNotFoundError):
        store.edit(key, REVENUE)
    with pytest.raises(MemoryNotFoundError):
        store.delete(key)
    assert len(store.entries().memories) == 1


def test_files_that_cannot_be_read_are_named(store: MemoryStore):
    store.add(REVENUE)
    folder = store.folder / "memories" / MEMORY_SCOPE
    (folder / "broken.json").write_text("{not json", encoding="utf-8")
    listing = store.entries()
    assert len(listing.memories) == 1
    assert listing.skipped == ["broken.json"]


@pytest.mark.parametrize(
    "body",
    [
        {"title": " ", "content": "x"},
        {"title": "x", "content": ""},
        {"title": "x" * (TITLE_LENGTH + 1), "content": "x"},
        {"title": "x", "content": "x" * (CONTENT_LENGTH + 1)},
        {"title": "x", "content": "x", "key": "chosen"},
    ],
    ids=["blank-title", "empty-content", "long-title", "long-content", "extra-key"],
)
def test_a_memory_needs_a_title_and_content_of_limited_length(body: dict):
    with pytest.raises(ValidationError):
        MemoryText.model_validate(body)


def test_the_text_is_trimmed():
    text = MemoryText.model_validate({"title": "  Revenue ", "content": " Completed. "})
    assert (text.title, text.content) == ("Revenue", "Completed.")


async def test_the_agent_saves_up_to_the_limits_of_the_memory_tab(store: MemoryStore):
    await agent_tool(
        store,
        "save_memory",
        {
            "key": "k" * KEY_LENGTH,
            "title": "t" * TITLE_LENGTH,
            "content": "c" * CONTENT_LENGTH,
        },
    )
    [entry] = store.entries().memories
    edited = store.edit(entry.key, MemoryText(title="Short", content=entry.content))
    assert edited.title == "Short"


@pytest.mark.parametrize(
    "change",
    [
        {"title": "t" * (TITLE_LENGTH + 1)},
        {"content": "c" * (CONTENT_LENGTH + 1)},
        {"title": " "},
        {"key": "k" * (KEY_LENGTH + 1)},
        {"key": ".."},
        {"key": "-revenue"},
        {"key": "revenue rule"},
        {"key": "a/b"},
    ],
    ids=[
        "long-title",
        "long-content",
        "blank-title",
        "long-key",
        "dots",
        "leading-dash",
        "space",
        "slash",
    ],
)
async def test_the_agent_cannot_save_what_the_memory_tab_could_not_edit(
    store: MemoryStore, change: dict
):
    memory = {"key": "revenue", "title": "Revenue", "content": "Completed only."}
    with pytest.raises(ValidationError):
        await agent_tool(store, "save_memory", {**memory, **change})
    assert store.entries().memories == []


def write_memory(store: MemoryStore, name: str, memory: Memory) -> None:
    folder = store.folder / "memories" / MEMORY_SCOPE
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(memory.model_dump_json(), encoding="utf-8")


def test_a_file_named_unlike_its_key_is_left_out(store: MemoryStore):
    entry = store.add(REVENUE)
    copy = Memory(key=entry.key, title="Copy", content="A copied file.")
    write_memory(store, "copy.json", copy)
    listing = store.entries()
    assert [memory.title for memory in listing.memories] == [REVENUE.title]
    assert listing.skipped == ["copy.json"]


@pytest.mark.parametrize("scope", ["other", "..", "user:42"])
def test_a_file_of_another_scope_is_left_out_and_not_changed(
    store: MemoryStore, scope: str
):
    write_memory(
        store, "hand.json", Memory(key="hand", title="T", content="C", scope=scope)
    )
    listing = store.entries()
    assert listing.memories == []
    assert listing.skipped == ["hand.json"]
    with pytest.raises(MemoryNotFoundError):
        store.edit("hand", REVENUE)
    with pytest.raises(MemoryNotFoundError):
        store.delete("hand")
    assert memory_files(store) == ["hand.json"]
    assert [path.name for path in (store.folder / "memories").iterdir()] == [
        MEMORY_SCOPE
    ]
