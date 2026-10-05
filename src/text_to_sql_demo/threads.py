import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from text_to_sql_demo.errors import (
    ThreadNotFoundError,
    TurnNotEditableError,
    TurnNotFoundError,
    VersionNotFoundError,
)
from text_to_sql_demo.frames import Frame

MAIN_BRANCH = "main"
TITLE_LENGTH = 80

type Role = Literal["user", "assistant"]
type TurnStatus = Literal["completed", "stopped", "error", "waiting"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def thread_title(message: str) -> str:
    """Return ``message`` as a title: on one line, at most ``TITLE_LENGTH`` characters."""
    return " ".join(message.split())[:TITLE_LENGTH]


class Version(BaseModel):
    """Which version of an edited user turn is shown: ``index`` counts from 0."""

    index: int
    count: int


class Turn(BaseModel):
    """One user message or one assistant answer, as the API shows it.

    ``version`` is set on user turns that have more than one version.
    ``answers`` is the key of the clarification a user turn answers.
    """

    id: str
    role: Role
    text: str
    frames: list[Frame]
    status: TurnStatus
    version: Version | None = None
    answers: str | None = None


class StoredTurn(BaseModel):
    """One turn as stored.

    Versions of a user turn share ``id`` and ``parent`` and differ in
    ``node``. ``branch`` names the line of turns the agent continues;
    ``answers`` is the clarification key for a user turn that answers one.
    """

    id: str
    node: str
    parent: str | None
    branch: str
    role: Role
    text: str
    frames: list[Frame]
    status: TurnStatus
    answers: str | None = None


class ThreadSummary(BaseModel):
    """A thread in the thread list, with the id of the database it is bound to."""

    id: str
    title: str
    database: str
    updated_at: datetime


class ThreadDetail(BaseModel):
    """A thread with the turns it shows, for replay."""

    id: str
    title: str
    database: str
    turns: list[Turn]


class ThreadRecord(ThreadSummary):
    """A stored thread: every turn of every version and the shown versions.

    ``title`` is the title the thread was created with. ``selected`` maps a
    user turn id to the index of its shown version.
    """

    turns: list[StoredTurn] = Field(default_factory=list)
    selected: dict[str, int] = Field(default_factory=dict)

    def shown(self) -> list[StoredTurn]:
        """Return the turns the thread shows, following the selected versions."""
        children: dict[str | None, list[StoredTurn]] = {}
        for turn in self.turns:
            children.setdefault(turn.parent, []).append(turn)
        shown: list[StoredTurn] = []
        parent: str | None = None
        while options := children.get(parent):
            turn = options[self.selected.get(options[0].id, 0)]
            shown.append(turn)
            parent = turn.node
        return shown

    def shown_title(self) -> str:
        """Return the title of the first user message shown, or ``title`` for an empty thread."""
        question = next((turn for turn in self.shown() if turn.role == "user"), None)
        return self.title if question is None else thread_title(question.text)

    def head(self) -> StoredTurn | None:
        """Return the last shown turn, or ``None`` for an empty thread."""
        shown = self.shown()
        return shown[-1] if shown else None

    def shown_turn(self, turn_id: str) -> StoredTurn:
        """Return the shown turn with ``turn_id``.

        Raises
        ------
        TurnNotFoundError
            If no shown turn has this id.
        """
        for turn in self.shown():
            if turn.id == turn_id:
                return turn
        raise TurnNotFoundError(f"Thread {self.id} shows no turn {turn_id}.")

    def versions(self, turn: StoredTurn) -> list[StoredTurn]:
        """Return every version of ``turn`` in the order they were made."""
        return [
            other
            for other in self.turns
            if other.id == turn.id and other.parent == turn.parent
        ]

    def detail(self) -> ThreadDetail:
        """Return the thread as the API shows it."""
        return ThreadDetail(
            id=self.id,
            title=self.shown_title(),
            database=self.database,
            turns=[self._turn(turn) for turn in self.shown()],
        )

    def _turn(self, turn: StoredTurn) -> Turn:
        versions = self.versions(turn)
        version = (
            Version(index=versions.index(turn), count=len(versions))
            if len(versions) > 1
            else None
        )
        return Turn(
            id=turn.id,
            role=turn.role,
            text=turn.text,
            frames=turn.frames,
            status=turn.status,
            version=version,
            answers=turn.answers,
        )


class ThreadFile(BaseModel):
    threads: list[ThreadRecord] = Field(default_factory=list)


class ThreadIndex:
    """Threads and their turns in one JSON file.

    Every change rewrites the file through a temporary file and a rename.
    A change that fails a check writes nothing. Reads and changes take the
    same lock, so no read has the file open while it is replaced.

    Parameters
    ----------
    path
        The JSON file, usually ``<data dir>/threads.json``. It and its
        folder are created on the first write.
    clock
        Returns the current time for ``updated_at``.
    """

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()

    def summaries(self) -> list[ThreadSummary]:
        """Return every thread, the most recently updated first.

        A thread's title is its first user message as shown, so it follows
        the version that is shown.
        """
        records = sorted(self._read().threads, key=lambda r: r.updated_at, reverse=True)
        return [
            ThreadSummary(
                id=r.id,
                title=r.shown_title(),
                database=r.database,
                updated_at=r.updated_at,
            )
            for r in records
        ]

    def get(self, thread_id: str) -> ThreadDetail:
        """Return one thread with the turns it shows.

        Raises
        ------
        ThreadNotFoundError
            If no thread has this id.
        """
        return self.record(thread_id).detail()

    def record(self, thread_id: str) -> ThreadRecord:
        """Return the stored thread with every version.

        Raises
        ------
        ThreadNotFoundError
            If no thread has this id.
        """
        return self._find(self._read(), thread_id)

    def create(self, title: str, database: str) -> str:
        """Store a new empty thread bound to ``database`` and return its id."""
        record = ThreadRecord(
            id=new_id(), title=title, database=database, updated_at=self._clock()
        )
        with self._lock:
            stored = self._load()
            stored.threads.append(record)
            self._save(stored)
        return record.id

    def add_user_turn(
        self, thread_id: str, text: str, *, answers: str | None = None
    ) -> StoredTurn:
        """Append a user turn after the last shown turn, on its branch.

        Parameters
        ----------
        thread_id
            The thread.
        text
            The message, or the answer when ``answers`` is set.
        answers
            The key of the clarification this turn answers.

        Raises
        ------
        ThreadNotFoundError
            If no thread has this id.
        """

        def change(record: ThreadRecord) -> StoredTurn:
            head = record.head()
            node = new_id()
            turn = StoredTurn(
                id=node,
                node=node,
                parent=None if head is None else head.node,
                branch=MAIN_BRANCH if head is None else head.branch,
                role="user",
                text=text,
                frames=[],
                status="completed",
                answers=answers,
            )
            record.turns.append(turn)
            return turn

        return self._change(thread_id, change)

    def add_version(self, thread_id: str, turn_id: str, text: str) -> StoredTurn:
        """Add a version of a shown user turn on a new branch and show it.

        The new version replaces the turn and everything after it in the
        shown thread; earlier versions keep their turns.

        Raises
        ------
        ThreadNotFoundError
            If no thread has this id.
        TurnNotFoundError
            If the thread shows no turn ``turn_id``.
        TurnNotEditableError
            If the turn is an assistant turn or answers a clarification.
        """

        def change(record: ThreadRecord) -> StoredTurn:
            turn = self._user_turn(record, turn_id, "edited")
            version = StoredTurn(
                id=turn.id,
                node=new_id(),
                parent=turn.parent,
                branch=new_id(),
                role="user",
                text=text,
                frames=[],
                status="completed",
            )
            record.turns.append(version)
            record.selected[turn.id] = len(record.versions(turn)) - 1
            return version

        return self._change(thread_id, change)

    def add_assistant_turn(
        self,
        thread_id: str,
        parent: str,
        text: str,
        frames: list[Frame],
        status: TurnStatus,
    ) -> StoredTurn:
        """Store the answer to the user turn with node ``parent``.

        The answer goes on that turn's branch, also when another version is
        shown by now.

        Raises
        ------
        ThreadNotFoundError
            If no thread has this id.
        TurnNotFoundError
            If no stored turn has the node ``parent``.
        """

        def change(record: ThreadRecord) -> StoredTurn:
            before = self._node(record, parent)
            node = new_id()
            turn = StoredTurn(
                id=node,
                node=node,
                parent=before.node,
                branch=before.branch,
                role="assistant",
                text=text,
                frames=frames,
                status=status,
            )
            record.turns.append(turn)
            return turn

        return self._change(thread_id, change)

    def set_status(self, thread_id: str, node: str, status: TurnStatus) -> None:
        """Change the status of the stored turn with ``node``.

        Raises
        ------
        ThreadNotFoundError
            If no thread has this id.
        TurnNotFoundError
            If no stored turn has this node.
        """

        def change(record: ThreadRecord) -> None:
            self._node(record, node).status = status

        self._change(thread_id, change)

    def select_version(self, thread_id: str, turn_id: str, index: int) -> ThreadDetail:
        """Show version ``index`` of a shown user turn and return the thread.

        Raises
        ------
        ThreadNotFoundError
            If no thread has this id.
        TurnNotFoundError
            If the thread shows no turn ``turn_id``.
        TurnNotEditableError
            If the turn is an assistant turn or answers a clarification.
        VersionNotFoundError
            If the turn has no version ``index``.
        """

        def change(record: ThreadRecord) -> ThreadDetail:
            turn = self._user_turn(record, turn_id, "switched")
            count = len(record.versions(turn))
            if not 0 <= index < count:
                raise VersionNotFoundError(
                    f"Turn {turn_id} has no version {index}; it has {count} "
                    f"(0 to {count - 1})."
                )
            record.selected[turn.id] = index
            return record.detail()

        return self._change(thread_id, change)

    def _change[T](self, thread_id: str, change: Callable[[ThreadRecord], T]) -> T:
        with self._lock:
            stored = self._load()
            record = self._find(stored, thread_id)
            result = change(record)
            record.updated_at = self._clock()
            self._save(stored)
        return result

    def _read(self) -> ThreadFile:
        with self._lock:
            return self._load()

    def _load(self) -> ThreadFile:
        if not self.path.is_file():
            return ThreadFile()
        return ThreadFile.model_validate_json(self.path.read_bytes())

    def _save(self, stored: ThreadFile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        temporary.write_text(stored.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _find(stored: ThreadFile, thread_id: str) -> ThreadRecord:
        for record in stored.threads:
            if record.id == thread_id:
                return record
        raise ThreadNotFoundError(f"Thread {thread_id} not found.")

    @staticmethod
    def _node(record: ThreadRecord, node: str) -> StoredTurn:
        for turn in record.turns:
            if turn.node == node:
                return turn
        raise TurnNotFoundError(f"Thread {record.id} has no stored turn {node}.")

    @staticmethod
    def _user_turn(record: ThreadRecord, turn_id: str, action: str) -> StoredTurn:
        turn = record.shown_turn(turn_id)
        if turn.role == "assistant":
            raise TurnNotEditableError(
                f"Turn {turn_id} is an assistant turn. Only user messages can be "
                f"{action}."
            )
        if turn.answers is not None:
            raise TurnNotEditableError(
                f"Turn {turn_id} is the answer to question {turn.answers!r}. Only "
                f"user messages can be {action}; edit the message before it."
            )
        return turn
