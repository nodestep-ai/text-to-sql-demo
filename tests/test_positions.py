from pathlib import Path

import pytest

from text_to_sql_demo.errors import TurnPositionNotFoundError
from text_to_sql_demo.positions import AgentPositions, BranchPosition, TurnStart


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "agent.json"


def test_nothing_is_known_before_the_first_write(path: Path):
    positions = AgentPositions(path)
    assert positions.branch("t1", "main") is None
    assert not path.exists()


def test_branches_and_turns_survive_a_new_instance(path: Path):
    positions = AgentPositions(path)
    positions.set_branch(
        "t1", "main", BranchPosition(nodestep_branch="main", last_completed="e1")
    )
    positions.set_turn("t1", "u1", TurnStart(nodestep_branch="main", before="e0"))
    positions.set_branch(
        "t2",
        "b2",
        BranchPosition(nodestep_branch="f1", last_completed="e7", status="waiting"),
    )
    again = AgentPositions(path)
    assert again.branch("t1", "main") == BranchPosition(
        nodestep_branch="main", last_completed="e1", status="settled"
    )
    assert again.turn("t1", "u1") == TurnStart(nodestep_branch="main", before="e0")
    position = again.branch("t2", "b2")
    assert position is not None
    assert position.status == "waiting"
    assert again.branch("t2", "main") is None


def test_a_write_replaces_the_branch_position(path: Path):
    positions = AgentPositions(path)
    positions.set_branch(
        "t1", "main", BranchPosition(nodestep_branch="main", last_completed="e1")
    )
    positions.set_branch(
        "t1",
        "main",
        BranchPosition(nodestep_branch="f2", last_completed="e9", status="unsettled"),
    )
    assert AgentPositions(path).branch("t1", "main") == BranchPosition(
        nodestep_branch="f2", last_completed="e9", status="unsettled"
    )
    assert [p.name for p in path.parent.iterdir()] == ["agent.json"]


def test_an_unknown_turn_is_an_error(path: Path):
    positions = AgentPositions(path)
    with pytest.raises(TurnPositionNotFoundError, match="u9"):
        positions.turn("t1", "u9")
