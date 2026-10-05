import asyncio
from collections.abc import AsyncGenerator, Iterable, Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from text_to_sql_demo.errors import ScriptExhaustedError
from text_to_sql_demo.frames import Frame


class ChatAgent(Protocol):
    """What the app needs from an agent.

    Implement ``run``, ``resume`` and ``edit`` as ``async def`` generators
    that ``yield`` frames. The app sends ``run`` before and ``done`` after the
    agent's frames and turns an exception into an ``error`` frame, so agents
    yield neither ``run`` nor ``done``. A clarification ends the stream; the
    answer arrives later through ``resume``.

    Every call names where it happens: ``thread_id``; ``database``, the id of
    the thread's database, opened through the ``DatabaseRegistry``; and
    ``branch``, the line of turns the call continues. The first branch is
    ``"main"``. Each edit starts a new branch, and every later turn on that
    version keeps it. Switching versions calls no agent method: the next
    call names the branch of the version that is shown. ``turn_id`` is the
    user turn the call answers; an agent keeps what it needs to find the
    state before that turn again, because ``edit`` names the same id.

    The app runs every stream in its own task. When the user stops a run, or
    the client goes away, the app cancels that task: ``CancelledError`` is
    raised inside the stream at the point where it waits. The stream must
    let it propagate and clean up in ``finally``. The app then calls
    ``cancel``.
    """

    def run(
        self, *, thread_id: str, database: str, branch: str, turn_id: str, message: str
    ) -> AsyncGenerator[Frame, None]:
        """Answer ``message``, the user turn ``turn_id``, at the end of ``branch``.

        A nodestep agent runs the graph on the nodestep branch it keeps for
        ``branch`` with ``thread_id`` passed explicitly, and records the state
        before the turn under ``turn_id``.
        """
        ...

    def resume(
        self,
        *,
        thread_id: str,
        database: str,
        branch: str,
        turn_id: str,
        key: str,
        answer: str,
    ) -> AsyncGenerator[Frame, None]:
        """Continue ``branch`` with ``answer`` to the clarification ``key``.

        ``answer`` is the id of one of the proposals, or free text when the
        question allowed it. ``turn_id`` is the user turn holding the answer.
        A nodestep agent resumes the interrupt with ``Resume`` on that branch.
        """
        ...

    def edit(
        self, *, thread_id: str, database: str, branch: str, turn_id: str, message: str
    ) -> AsyncGenerator[Frame, None]:
        """Answer ``message`` as a new version of the user turn ``turn_id``.

        ``branch`` is new. A nodestep agent forks the thread at the state
        before ``turn_id`` (``graph.fork``), keeps the fork as the nodestep
        branch for ``branch`` and runs ``message`` there. The versions that
        existed before stay as they are.
        """
        ...

    async def cancel(self, *, thread_id: str, database: str, branch: str) -> None:
        """Forget the unfinished work on ``branch`` after the user stopped it.

        The app calls this after it cancelled a run on ``branch``, and when
        the user stops a thread that waits for a clarification. The next call
        on ``branch`` must start from the last completed state: a nodestep
        agent forks from the last completed superstep, which drops the
        cancelled superstep and any pending interrupt, and continues ``branch``
        on that fork.
        """
        ...


class Delay(BaseModel):
    """A pause in a ``ScriptedAgent`` script."""

    seconds: float = Field(ge=0)


type Step = Frame | Exception | Delay


class AgentCall(BaseModel):
    """One call recorded by ``ScriptedAgent``."""

    method: Literal["run", "resume", "edit", "cancel"]
    thread_id: str
    database: str
    branch: str
    turn_id: str | None = None
    message: str | None = None
    key: str | None = None
    answer: str | None = None


class ScriptedAgent:
    """A test agent that plays prepared scripts, one per streaming call.

    A frame in a script is yielded, an exception is raised at that point and
    a ``Delay`` waits, so a long delay makes a stream that only ends when it
    is stopped. ``calls`` records every call, ``cancel`` included; ``cancel``
    uses no script.

    Parameters
    ----------
    scripts
        One sequence per ``run``, ``resume`` or ``edit`` call, in call order.
    """

    def __init__(self, scripts: Iterable[Sequence[Step]] = ()) -> None:
        self.scripts = [list(script) for script in scripts]
        self.calls: list[AgentCall] = []
        self._played = 0

    async def run(
        self, *, thread_id: str, database: str, branch: str, turn_id: str, message: str
    ) -> AsyncGenerator[Frame, None]:
        """Record the call and play its script."""
        call = AgentCall(
            method="run",
            thread_id=thread_id,
            database=database,
            branch=branch,
            turn_id=turn_id,
            message=message,
        )
        async for frame in self._play(call):
            yield frame

    async def resume(
        self,
        *,
        thread_id: str,
        database: str,
        branch: str,
        turn_id: str,
        key: str,
        answer: str,
    ) -> AsyncGenerator[Frame, None]:
        """Record the call and play its script."""
        call = AgentCall(
            method="resume",
            thread_id=thread_id,
            database=database,
            branch=branch,
            turn_id=turn_id,
            key=key,
            answer=answer,
        )
        async for frame in self._play(call):
            yield frame

    async def edit(
        self, *, thread_id: str, database: str, branch: str, turn_id: str, message: str
    ) -> AsyncGenerator[Frame, None]:
        """Record the call and play its script."""
        call = AgentCall(
            method="edit",
            thread_id=thread_id,
            database=database,
            branch=branch,
            turn_id=turn_id,
            message=message,
        )
        async for frame in self._play(call):
            yield frame

    async def cancel(self, *, thread_id: str, database: str, branch: str) -> None:
        """Record the call."""
        self.calls.append(
            AgentCall(
                method="cancel", thread_id=thread_id, database=database, branch=branch
            )
        )

    def script_for(self, call: AgentCall) -> Sequence[Step]:
        """Return the script for ``call``: the next one in call order.

        Subclasses override this to choose a script from the call.

        Raises
        ------
        ScriptExhaustedError
            If every script has been played.
        """
        self._played += 1
        if self._played > len(self.scripts):
            raise ScriptExhaustedError(
                f"ScriptedAgent has no script left for call {self._played}"
            )
        return self.scripts[self._played - 1]

    async def _play(self, call: AgentCall) -> AsyncGenerator[Frame, None]:
        self.calls.append(call)
        for step in self.script_for(call):
            if isinstance(step, Delay):
                await asyncio.sleep(step.seconds)
            elif isinstance(step, Exception):
                raise step
            else:
                yield step
