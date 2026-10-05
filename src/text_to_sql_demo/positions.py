import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from text_to_sql_demo.errors import TurnPositionNotFoundError

type BranchStatus = Literal["settled", "waiting", "unsettled"]


class BranchPosition(BaseModel):
    """Where a text-to-sql-demo branch stands in its nodestep thread.

    ``status`` is ``settled`` after a finished run, ``waiting`` while a
    clarification is open and ``unsettled`` after a run that was stopped or
    failed; such a branch continues on a fork of ``last_completed``.
    """

    nodestep_branch: str
    last_completed: str
    status: BranchStatus = "settled"


class TurnStart(BaseModel):
    """The state before a user turn: an event of a nodestep branch."""

    nodestep_branch: str
    before: str


class ThreadPositions(BaseModel):
    """The branch positions and turn starts of one thread."""

    branches: dict[str, BranchPosition] = Field(default_factory=dict)
    turns: dict[str, TurnStart] = Field(default_factory=dict)


class PositionsFile(BaseModel):
    threads: dict[str, ThreadPositions] = Field(default_factory=dict)


class AgentPositions:
    """What the nodestep agent keeps beside the nodestep state store, in one JSON file.

    Every change rewrites the file through a temporary file and a rename,
    under a lock that reads take too.

    Parameters
    ----------
    path
        The JSON file, usually ``<data dir>/agent.json``. It and its folder
        are created on the first write.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def branch(self, thread_id: str, branch: str) -> BranchPosition | None:
        """Return the position of ``branch``, or ``None`` when it has none yet."""
        with self._lock:
            thread = self._load().threads.get(thread_id)
        return None if thread is None else thread.branches.get(branch)

    def set_branch(self, thread_id: str, branch: str, position: BranchPosition) -> None:
        """Store the position of ``branch``."""
        self._change(
            thread_id, lambda thread: thread.branches.update({branch: position})
        )

    def turn(self, thread_id: str, turn_id: str) -> TurnStart:
        """Return where the user turn ``turn_id`` started.

        Raises
        ------
        TurnPositionNotFoundError
            If the turn was not answered by this agent.
        """
        with self._lock:
            thread = self._load().threads.get(thread_id)
        start = None if thread is None else thread.turns.get(turn_id)
        if start is None:
            raise TurnPositionNotFoundError(
                f"Thread {thread_id} has no recorded state before turn {turn_id}, "
                "so the turn cannot be edited."
            )
        return start

    def set_turn(self, thread_id: str, turn_id: str, start: TurnStart) -> None:
        """Store where the user turn ``turn_id`` started."""
        self._change(thread_id, lambda thread: thread.turns.update({turn_id: start}))

    def _change(
        self, thread_id: str, change: Callable[[ThreadPositions], None]
    ) -> None:
        with self._lock:
            stored = self._load()
            change(stored.threads.setdefault(thread_id, ThreadPositions()))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.tmp")
            temporary.write_text(stored.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(self.path)

    def _load(self) -> PositionsFile:
        if not self.path.is_file():
            return PositionsFile()
        return PositionsFile.model_validate_json(self.path.read_bytes())
