from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from nodestep import ScriptedChat
from nodestep.chat import Chat, ChatRequest
from nodestep.middleware import SkillConflictError, SkillFormatError

from harness import calls, tool_call
from text_to_sql_demo.frames import Frame, ToolFrame
from text_to_sql_demo.memory import MemoryStore, MemoryText
from text_to_sql_demo.nodestep_agent import SHIPPED_SKILLS, NodestepAgent, skill_roots
from text_to_sql_demo.settings import ServerSettings

WHERE = {"thread_id": "t1", "database": "fixture", "branch": "main"}
REVENUE = {
    "key": "revenue",
    "title": "Revenue rule",
    "content": "Revenue counts completed orders only.",
}
OWN_SKILL = """---
name: courses
description: How to count courses per teacher
---
Group the Course table by TeacherId.
"""


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


async def collect(frames: AsyncGenerator[Frame, None]) -> list[Frame]:
    return [frame async for frame in frames]


async def tool_rows(
    settings: ServerSettings, name: str, arguments: dict, turn: str = "u1"
) -> list[ToolFrame]:
    chat = ScriptedChat([calls(tool_call(name, arguments, f"c-{turn}")), "Done."])
    frames = await collect(
        make_agent(settings, chat).run(**WHERE, turn_id=turn, message="Hi")
    )
    return [frame for frame in frames if isinstance(frame, ToolFrame)]


def skills_listed(request: ChatRequest) -> str:
    return "\n".join(
        message.content or ""
        for message in request.messages
        if message.type == "system" and "Available skills" in (message.content or "")
    )


async def test_a_saved_memory_shows_its_title_and_is_listed_for_the_web_app(
    server_settings: ServerSettings,
):
    started, finished = await tool_rows(server_settings, "save_memory", REVENUE)
    assert (started.name, started.status) == ("save_memory", "started")
    assert started.arguments == REVENUE
    assert (finished.status, finished.summary) == ("finished", "Revenue rule")
    [entry] = MemoryStore(server_settings.memory_folder).entries().memories
    assert (entry.key, entry.content) == ("revenue", REVENUE["content"])


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"title": "t" * 121}, "at most 120 characters"),
        ({"content": "c" * 2001}, "at most 2000 characters"),
        ({"key": "revenue rule"}, "should match pattern"),
    ],
)
async def test_a_memory_the_memory_tab_could_not_edit_is_a_tool_error(
    server_settings: ServerSettings, change: dict, problem: str
):
    _, failed = await tool_rows(server_settings, "save_memory", {**REVENUE, **change})
    assert failed.status == "error"
    assert problem in (failed.summary or "")
    assert MemoryStore(server_settings.memory_folder).entries().memories == []


async def test_a_memory_search_names_what_it_found(server_settings: ServerSettings):
    store = MemoryStore(server_settings.memory_folder)
    store.add(MemoryText(title="Revenue rule", content="Completed orders only."))
    _, found = await tool_rows(server_settings, "search_memory", {"query": "revenue"})
    assert found.summary == "1 match: Revenue rule"
    store.add(MemoryText(title="Revenue year", content="The year starts in April."))
    _, found = await tool_rows(server_settings, "search_memory", {"query": "revenue"})
    assert found.summary is not None
    assert found.summary.startswith("2 matches: Revenue ")
    _, missed = await tool_rows(server_settings, "search_memory", {"query": "stores"})
    assert (missed.status, missed.summary) == ("finished", "No match")


async def test_listing_the_memory_counts_it(server_settings: ServerSettings):
    MemoryStore(server_settings.memory_folder).add(
        MemoryText(title="One", content="First.")
    )
    _, listed = await tool_rows(server_settings, "list_memories", {})
    assert listed.summary == "1 memory"


async def test_a_delete_names_the_key_and_a_missing_key_is_an_error(
    server_settings: ServerSettings,
):
    await tool_rows(server_settings, "save_memory", REVENUE)
    _, deleted = await tool_rows(
        server_settings, "delete_memory", {"key": "revenue"}, "u3"
    )
    assert (deleted.status, deleted.summary) == ("finished", "revenue")
    assert MemoryStore(server_settings.memory_folder).entries().memories == []
    _, missing = await tool_rows(
        server_settings, "delete_memory", {"key": "revenue"}, "u5"
    )
    assert (missing.status, missing.summary) == (
        "error",
        "No memory has the key 'revenue'.",
    )


async def test_the_shipped_skills_are_listed_and_load(server_settings: ServerSettings):
    chat = ScriptedChat(
        [calls(tool_call("load_skill", {"name": "report"}, "c1")), "Done."]
    )
    frames = await collect(
        make_agent(server_settings, chat).run(**WHERE, turn_id="u1", message="Hi")
    )
    listed = skills_listed(chat.requests[0])
    assert "- report: How to put together a sales report" in listed
    assert "- sqlite-dates: SQLite date functions" in listed
    loaded = [frame for frame in frames if isinstance(frame, ToolFrame)][-1]
    assert (loaded.name, loaded.status) == ("load_skill", "finished")
    assert loaded.summary == (
        "How to put together a sales report, such as sales by store for a year"
    )
    skill_result = chat.requests[1].messages[-1].content or ""
    assert "Call analyze_topics with these topics" in skill_result


async def test_an_unknown_skill_is_a_tool_error(server_settings: ServerSettings):
    [_, failed] = await tool_rows(server_settings, "load_skill", {"name": "nope"})
    assert (failed.status, failed.summary) == ("error", "No skill is named 'nope'.")


async def test_a_skill_dropped_in_the_skills_folder_is_offered(
    server_settings: ServerSettings,
):
    folder = server_settings.skills_folder / "courses"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(OWN_SKILL, encoding="utf-8")
    chat = ScriptedChat(
        [calls(tool_call("load_skill", {"name": "courses"}, "c1")), "Done."]
    )
    await collect(
        make_agent(server_settings, chat).run(**WHERE, turn_id="u1", message="Hi")
    )
    assert "- courses: How to count courses per teacher" in skills_listed(
        chat.requests[0]
    )
    assert "Group the Course table" in (chat.requests[1].messages[-1].content or "")


def test_an_empty_or_missing_skills_folder_adds_nothing(tmp_path: Path):
    assert skill_roots(tmp_path / "missing") == [SHIPPED_SKILLS]
    (tmp_path / "empty").mkdir()
    assert skill_roots(tmp_path / "empty") == [SHIPPED_SKILLS]
    assert sorted(path.parent.name for path in SHIPPED_SKILLS.rglob("SKILL.md")) == [
        "report",
        "sqlite-dates",
    ]


def test_a_broken_skill_stops_the_start(server_settings: ServerSettings):
    folder = server_settings.skills_folder / "broken"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
    with pytest.raises(SkillFormatError):
        make_agent(server_settings, ScriptedChat())


def test_a_skill_named_like_a_shipped_one_stops_the_start(
    server_settings: ServerSettings,
):
    folder = server_settings.skills_folder / "report"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        OWN_SKILL.replace("name: courses", "name: report"), encoding="utf-8"
    )
    with pytest.raises(SkillConflictError):
        make_agent(server_settings, ScriptedChat())
