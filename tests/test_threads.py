import json
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from samples import PROPOSALS, SAMPLES
from text_to_sql_demo.errors import (
    ThreadNotFoundError,
    TurnNotEditableError,
    TurnNotFoundError,
    VersionNotFoundError,
)
from text_to_sql_demo.frames import ClarificationFrame, TokenFrame
from text_to_sql_demo.threads import MAIN_BRANCH, StoredTurn, ThreadFile, ThreadIndex

HEX_ID = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def clock() -> Callable[[], datetime]:
    ticks: Iterator[datetime] = (
        datetime(2026, 9, 30, 12, 0, tzinfo=UTC) + timedelta(seconds=n)
        for n in range(1000)
    )
    return lambda: next(ticks)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "threads.json"


@pytest.fixture
def index(path: Path, clock: Callable[[], datetime]) -> ThreadIndex:
    return ThreadIndex(path, clock=clock)


def exchange(index: ThreadIndex, thread_id: str, question: str) -> StoredTurn:
    user = index.add_user_turn(thread_id, question)
    return index.add_assistant_turn(
        thread_id, user.node, f"answer to {question}", [], "completed"
    )


@pytest.fixture
def three(index: ThreadIndex) -> str:
    thread_id = index.create("q1", "demo_shop")
    for question in ("q1", "q2", "q3"):
        exchange(index, thread_id, question)
    return thread_id


def texts(index: ThreadIndex, thread_id: str) -> list[str]:
    return [turn.text for turn in index.get(thread_id).turns]


def test_create_returns_a_new_id(index: ThreadIndex):
    thread_id = index.create("How many products?", "demo_shop")
    assert HEX_ID.fullmatch(thread_id)
    [summary] = index.summaries()
    assert summary.id == thread_id
    assert summary.title == "How many products?"
    assert summary.database == "demo_shop"
    assert summary.updated_at == datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def test_turns_get_ids_and_statuses(index: ThreadIndex):
    thread_id = index.create("q", "demo_shop")
    before = index.summaries()[0].updated_at
    user = index.add_user_turn(thread_id, "q")
    assistant = index.add_assistant_turn(
        thread_id, user.node, "a", [TokenFrame(text="a")], "stopped"
    )
    detail = index.get(thread_id)
    assert [(turn.role, turn.status) for turn in detail.turns] == [
        ("user", "completed"),
        ("assistant", "stopped"),
    ]
    assert [turn.id for turn in detail.turns] == [user.id, assistant.id]
    assert all(HEX_ID.fullmatch(turn.id) for turn in detail.turns)
    assert user.id != assistant.id
    assert detail.turns[1].frames == [TokenFrame(text="a")]
    assert index.summaries()[0].updated_at > before


def test_first_turns_are_on_the_main_branch(index: ThreadIndex):
    thread_id = index.create("q", "demo_shop")
    user = index.add_user_turn(thread_id, "q")
    assistant = index.add_assistant_turn(thread_id, user.node, "a", [], "completed")
    assert user.branch == MAIN_BRANCH
    assert assistant.branch == MAIN_BRANCH
    assert user.parent is None
    assert assistant.parent == user.node


def test_detail_dump_has_the_contract_keys(index: ThreadIndex):
    thread_id = index.create("q", "demo_shop")
    user = index.add_user_turn(thread_id, "q")
    index.add_assistant_turn(thread_id, user.node, "a", [], "completed")
    dumped = index.get(thread_id).model_dump(mode="json")
    assert set(dumped) == {"id", "title", "database", "turns"}
    assert dumped["database"] == "demo_shop"
    assert dumped["turns"][0] == {
        "id": user.id,
        "role": "user",
        "text": "q",
        "frames": [],
        "status": "completed",
        "version": None,
        "answers": None,
    }
    assert set(dumped["turns"][1]) == {
        "id",
        "role",
        "text",
        "frames",
        "status",
        "version",
        "answers",
    }


def test_summaries_list_the_latest_update_first(index: ThreadIndex):
    first = index.create("first", "demo_shop")
    second = index.create("second", "demo_shop")
    assert [s.id for s in index.summaries()] == [second, first]
    index.add_user_turn(first, "again")
    assert [s.id for s in index.summaries()] == [first, second]


def test_unknown_thread_raises_without_writing(index: ThreadIndex, path: Path):
    with pytest.raises(ThreadNotFoundError, match="nope"):
        index.get("nope")
    with pytest.raises(ThreadNotFoundError):
        index.add_user_turn("nope", "x")
    with pytest.raises(ThreadNotFoundError):
        index.record("nope")
    assert not path.exists()


def test_a_second_index_reads_the_same_file(index: ThreadIndex, path: Path):
    thread_id = index.create("q", "demo_shop")
    user = index.add_user_turn(thread_id, "q")
    index.add_assistant_turn(thread_id, user.node, "", SAMPLES, "completed")
    reopened = ThreadIndex(path)
    assert reopened.get(thread_id) == index.get(thread_id)
    assert reopened.get(thread_id).turns[1].frames == SAMPLES
    assert reopened.record(thread_id) == index.record(thread_id)


def test_file_layout(index: ThreadIndex, path: Path):
    thread_id = index.create("q", "demo_shop")
    user = index.add_user_turn(thread_id, "q")
    index.add_assistant_turn(
        thread_id,
        user.node,
        "",
        [
            ClarificationFrame(
                key="clarify",
                question="Which?",
                proposals=PROPOSALS,
                allow_free_text=True,
            )
        ],
        "waiting",
    )
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert list(stored) == ["threads"]
    [thread] = stored["threads"]
    assert set(thread) == {
        "id",
        "title",
        "database",
        "updated_at",
        "turns",
        "selected",
    }
    assert thread["database"] == "demo_shop"
    assert set(thread["turns"][0]) == {
        "id",
        "node",
        "parent",
        "branch",
        "role",
        "text",
        "frames",
        "status",
        "answers",
    }
    assert thread["turns"][1]["frames"][0]["type"] == "clarification"
    assert not path.with_name("threads.json.tmp").exists()


def test_each_thread_keeps_its_database(index: ThreadIndex):
    sales = index.create("q", "sales")
    shop = index.create("q", "demo_shop")
    assert index.get(sales).database == "sales"
    assert index.get(shop).database == "demo_shop"
    summaries = {summary.id: summary.database for summary in index.summaries()}
    assert summaries == {sales: "sales", shop: "demo_shop"}


def test_summary_dump_has_the_contract_keys(index: ThreadIndex):
    index.create("q", "demo_shop")
    [summary] = index.summaries()
    assert set(summary.model_dump(mode="json")) == {
        "id",
        "title",
        "database",
        "updated_at",
    }


def test_set_status_changes_one_turn(index: ThreadIndex):
    thread_id = index.create("q", "demo_shop")
    waiting = index.add_assistant_turn(
        thread_id, index.add_user_turn(thread_id, "q").node, "", [], "waiting"
    )
    index.set_status(thread_id, waiting.node, "stopped")
    assert [turn.status for turn in index.get(thread_id).turns] == [
        "completed",
        "stopped",
    ]
    with pytest.raises(TurnNotFoundError):
        index.set_status(thread_id, "nope", "stopped")


def test_an_answer_turn_remembers_its_question(index: ThreadIndex):
    thread_id = index.create("q", "demo_shop")
    exchange(index, thread_id, "q")
    answer = index.add_user_turn(thread_id, "gross", answers="clarify")
    assert answer.answers == "clarify"
    assert index.record(thread_id).head() == answer


def test_editing_the_first_turn_starts_a_new_version(index: ThreadIndex, three: str):
    original = index.get(three)
    first = original.turns[0]
    version = index.add_version(three, first.id, "q1 again")
    assert version.id == first.id
    assert version.node != first.id
    assert version.parent is None
    assert version.branch != MAIN_BRANCH
    assert HEX_ID.fullmatch(version.branch)
    edited = index.get(three)
    assert [turn.text for turn in edited.turns] == ["q1 again"]
    assert edited.turns[0].id == first.id
    assert edited.turns[0].version is not None
    assert edited.turns[0].version.model_dump() == {"index": 1, "count": 2}
    reply = index.add_assistant_turn(three, version.node, "new", [], "completed")
    assert reply.branch == version.branch
    assert texts(index, three) == ["q1 again", "new"]


def test_switching_versions_back_and_forth(index: ThreadIndex, three: str):
    original = index.get(three)
    first = original.turns[0]
    version = index.add_version(three, first.id, "q1 again")
    index.add_assistant_turn(three, version.node, "new", [], "completed")
    back = index.select_version(three, first.id, 0)
    assert [turn.id for turn in back.turns] == [turn.id for turn in original.turns]
    assert [turn.text for turn in back.turns] == [
        "q1",
        "answer to q1",
        "q2",
        "answer to q2",
        "q3",
        "answer to q3",
    ]
    assert back.turns[0].version is not None
    assert back.turns[0].version.model_dump() == {"index": 0, "count": 2}
    assert all(turn.version is None for turn in back.turns[1:])
    assert index.get(three) == back
    forth = index.select_version(three, first.id, 1)
    assert [turn.text for turn in forth.turns] == ["q1 again", "new"]
    assert index.select_version(three, first.id, 0) == back


def test_new_turns_follow_the_shown_version(index: ThreadIndex, three: str):
    first = index.get(three).turns[0]
    version = index.add_version(three, first.id, "q1 again")
    index.add_assistant_turn(three, version.node, "new", [], "completed")
    index.select_version(three, first.id, 0)
    on_main = index.add_user_turn(three, "q4")
    assert on_main.branch == MAIN_BRANCH
    assert texts(index, three)[-2:] == ["answer to q3", "q4"]
    index.select_version(three, first.id, 1)
    on_edit = index.add_user_turn(three, "q5")
    assert on_edit.branch == version.branch
    assert texts(index, three) == ["q1 again", "new", "q5"]


def test_a_later_turn_can_be_edited_too(index: ThreadIndex, three: str):
    turns = index.get(three).turns
    version = index.add_version(three, turns[2].id, "q2 again")
    index.add_assistant_turn(three, version.node, "new", [], "completed")
    assert texts(index, three) == ["q1", "answer to q1", "q2 again", "new"]
    third = index.add_version(three, turns[2].id, "q2 third")
    assert third.branch not in (MAIN_BRANCH, version.branch)
    shown = index.get(three).turns
    assert shown[2].version is not None
    assert shown[2].version.model_dump() == {"index": 2, "count": 3}
    assert [turn.text for turn in shown] == ["q1", "answer to q1", "q2 third"]


def test_assistant_and_answer_turns_cannot_be_edited(index: ThreadIndex, path: Path):
    thread_id = index.create("q", "demo_shop")
    assistant = exchange(index, thread_id, "q")
    answer = index.add_user_turn(thread_id, "gross", answers="clarify")
    before = path.read_bytes()
    with pytest.raises(TurnNotEditableError, match="assistant"):
        index.add_version(thread_id, assistant.id, "x")
    with pytest.raises(TurnNotEditableError, match="answer"):
        index.add_version(thread_id, answer.id, "x")
    with pytest.raises(TurnNotEditableError, match="assistant"):
        index.select_version(thread_id, assistant.id, 0)
    assert path.read_bytes() == before


def test_turns_of_a_hidden_version_are_not_found(index: ThreadIndex, three: str):
    turns = index.get(three).turns
    index.add_version(three, turns[0].id, "q1 again")
    with pytest.raises(TurnNotFoundError, match=turns[2].id):
        index.add_version(three, turns[2].id, "x")
    with pytest.raises(TurnNotFoundError):
        index.select_version(three, turns[2].id, 0)
    with pytest.raises(TurnNotFoundError, match="nope"):
        index.add_version(three, "nope", "x")


@pytest.mark.parametrize("version", [-1, 2, 9])
def test_a_missing_version_is_not_found(
    index: ThreadIndex, three: str, path: Path, version: int
):
    first = index.get(three).turns[0]
    index.add_version(three, first.id, "q1 again")
    before = path.read_bytes()
    with pytest.raises(VersionNotFoundError, match=str(version)):
        index.select_version(three, first.id, version)
    assert path.read_bytes() == before


def test_an_unedited_turn_has_one_version(index: ThreadIndex, three: str):
    first = index.get(three).turns[0]
    assert index.select_version(three, first.id, 0) == index.get(three)
    with pytest.raises(VersionNotFoundError):
        index.select_version(three, first.id, 1)


class SlowIndex(ThreadIndex):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.saving = threading.Event()
        self.release = threading.Event()

    def _save(self, stored: ThreadFile) -> None:
        self.saving.set()
        assert self.release.wait(5)
        super()._save(stored)


@pytest.mark.parametrize("read", ["summaries", "get", "record"])
def test_reads_wait_for_a_write_in_progress(path: Path, read: str):
    index = SlowIndex(path)
    index.release.set()
    thread_id = index.create("q", "demo_shop")
    index.saving.clear()
    index.release.clear()
    writer = threading.Thread(target=exchange, args=(index, thread_id, "q1"))
    writer.start()
    assert index.saving.wait(5)
    results: list[object] = []
    reader = threading.Thread(
        target=lambda: results.append(
            index.summaries()
            if read == "summaries"
            else getattr(index, read)(thread_id)
        )
    )
    reader.start()
    reader.join(0.2)
    assert reader.is_alive()
    index.release.set()
    writer.join(5)
    reader.join(5)
    assert len(results) == 1


def test_an_answer_names_its_question_in_the_detail(index: ThreadIndex):
    thread_id = index.create("q", "demo_shop")
    question = index.add_user_turn(thread_id, "Revenue?")
    index.add_assistant_turn(thread_id, question.node, "", [SAMPLES[5]], "completed")
    index.add_user_turn(thread_id, "net", answers="clarify")
    assert [turn.answers for turn in index.get(thread_id).turns] == [
        None,
        None,
        "clarify",
    ]


def test_the_title_follows_the_shown_version_of_the_first_question(
    index: ThreadIndex,
):
    thread_id = index.create("Which ten products sold the most units?", "demo_shop")
    exchange(index, thread_id, "Which ten products sold the most units?")
    first = index.get(thread_id).turns[0]
    index.add_version(thread_id, first.id, "What is the\nrevenue   per month? ")
    assert index.get(thread_id).title == "What is the revenue per month?"
    assert [summary.title for summary in index.summaries()] == [
        "What is the revenue per month?"
    ]
    index.select_version(thread_id, first.id, 0)
    assert index.get(thread_id).title == "Which ten products sold the most units?"
    assert [summary.title for summary in index.summaries()] == [
        "Which ten products sold the most units?"
    ]
    index.add_version(thread_id, first.id, "x" * 90)
    assert index.get(thread_id).title == "x" * 80


def test_an_empty_thread_keeps_the_title_it_was_created_with(index: ThreadIndex):
    thread_id = index.create("Which store has the most returns?", "demo_shop")
    assert index.get(thread_id).title == "Which store has the most returns?"
