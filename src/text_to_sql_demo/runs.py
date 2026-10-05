import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager

from pydantic import BaseModel

from text_to_sql_demo.errors import RunActiveError
from text_to_sql_demo.frames import (
    ChartFrame,
    ClarificationFrame,
    DoneFrame,
    ErrorFrame,
    FinalFrame,
    Frame,
    RunFrame,
    SqlResultFrame,
    StoppedFrame,
    TokenFrame,
    ToolFrame,
)
from text_to_sql_demo.threads import TurnStatus

BLOCK_FRAMES = (ToolFrame, SqlResultFrame, ChartFrame, ClarificationFrame)


class RunResult(BaseModel):
    """What a run leaves for its assistant turn."""

    status: TurnStatus
    text: str
    frames: list[Frame]


class Run:
    """One agent stream on a thread, driven by its own task.

    The task reads the agent's frames and passes them on to ``events``
    between ``first`` and a ``done`` frame. It collects the assistant turn:
    every frame except ``final`` and ``stopped``, the ``final`` text (the
    joined tokens since the last tool, result, chart or clarification when
    there is none) and the status: ``error``
    when the agent sent an ``error`` frame or raised (sent as an
    ``agent_error`` frame), ``waiting`` after a clarification and
    ``stopped`` when the run was stopped (sent as a ``stopped`` frame). A
    run stopped before its task started never opens the agent's stream,
    but still sends ``stopped`` and awaits ``on_cancel``.

    Text the model streams before a tool, result, chart or clarification
    frame is kept as one ``token`` frame in front of it, so a replay shows it
    where the live stream did.

    Parameters
    ----------
    first
        The ``run`` frame that opens the stream.
    frames
        The agent's stream; it is closed when the run ends.
    on_cancel
        Awaited after the task was cancelled, before ``on_end``.
    on_end
        Called once with the result, however the run ended.
    on_close
        Called once after ``on_end``, even when ``on_end`` raised.
    """

    def __init__(
        self,
        first: RunFrame,
        frames: AsyncGenerator[Frame, None],
        *,
        on_cancel: Callable[[], Awaitable[None]],
        on_end: Callable[[RunResult], object],
        on_close: Callable[[], object],
    ) -> None:
        self._frames = frames
        self._on_cancel = on_cancel
        self._on_end = on_end
        self._on_close = on_close
        self._queue: asyncio.Queue[Frame] = asyncio.Queue()
        self._queue.put_nowait(first)
        self._recorded: list[Frame] = []
        self._tokens: list[str] = []
        self._final: str | None = None
        self._started = False
        self._stopping = False
        self._ended = False
        self._task = asyncio.create_task(self._drive())
        self._task.add_done_callback(self._closed)

    async def events(self) -> AsyncGenerator[Frame, None]:
        """Yield the frames of the run, ending with ``done``."""
        while True:
            frame = await self._queue.get()
            yield frame
            if isinstance(frame, DoneFrame):
                return

    async def stop(self) -> None:
        """Cancel the run once and wait until it has ended.

        Raises
        ------
        Exception
            What ``on_cancel`` or ``on_end`` raised.
        """
        if not self._stopping:
            self._stopping = True
            if self._started:
                self._task.cancel()
        await self.wait()

    async def wait(self) -> None:
        """Wait until the run has ended.

        Raises
        ------
        Exception
            What ``on_cancel`` or ``on_end`` raised.
        """
        await asyncio.wait({self._task})
        if not self._task.cancelled() and (error := self._task.exception()):
            raise error

    async def _drive(self) -> None:
        self._started = True
        status: TurnStatus = "stopped"
        try:
            if self._stopping:
                await self._frames.aclose()
                raise asyncio.CancelledError
            status = await self._play()
        except asyncio.CancelledError:
            status = "stopped"
            self._queue.put_nowait(StoppedFrame())
            await self._on_cancel()
            raise
        finally:
            self._end(status)

    async def _play(self) -> TurnStatus:
        try:
            async with aclosing(self._frames):
                async for frame in self._frames:
                    self._collect(frame)
                    self._queue.put_nowait(frame)
        except Exception as error:
            failure = ErrorFrame(
                kind="agent_error", message=str(error) or type(error).__name__
            )
            self._recorded.append(failure)
            self._queue.put_nowait(failure)
            return "error"
        if any(isinstance(frame, ErrorFrame) for frame in self._recorded):
            return "error"
        if any(isinstance(frame, ClarificationFrame) for frame in self._recorded):
            return "waiting"
        return "completed"

    def _collect(self, frame: Frame) -> None:
        if isinstance(frame, TokenFrame):
            self._tokens.append(frame.text)
        elif isinstance(frame, FinalFrame):
            self._final = frame.text
        else:
            if isinstance(frame, BLOCK_FRAMES) and self._tokens:
                self._recorded.append(TokenFrame(text="".join(self._tokens)))
                self._tokens = []
            self._recorded.append(frame)

    def _end(self, status: TurnStatus) -> None:
        if self._ended:
            return
        self._ended = True
        text = "".join(self._tokens) if self._final is None else self._final
        try:
            self._on_end(RunResult(status=status, text=text, frames=self._recorded))
        finally:
            self._on_close()
            self._queue.put_nowait(DoneFrame())

    def _closed(self, task: asyncio.Task[None]) -> None:
        self._end("stopped")


class RunRegistry:
    """The active run of each thread; a thread has at most one.

    A thread whose open question is being closed counts as busy too.
    """

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._closing: set[str] = set()

    def ensure_idle(self, thread_id: str) -> None:
        """Check that no run is active on the thread and nothing closes it.

        Raises
        ------
        RunActiveError
            If a run is active on the thread or its open question is being
            closed.
        """
        if thread_id in self._runs:
            raise RunActiveError(
                f"Thread {thread_id} is still answering. Wait for the answer or "
                f"stop it with POST /api/threads/{thread_id}/stop first."
            )
        if thread_id in self._closing:
            raise RunActiveError(
                f"Thread {thread_id} is closing its open question. Try again when "
                "the stop request has returned."
            )

    @asynccontextmanager
    async def closing(self, thread_id: str) -> AsyncIterator[None]:
        """Keep an idle thread busy while its open question is closed.

        Raises
        ------
        RunActiveError
            If the thread is not idle.
        """
        self.ensure_idle(thread_id)
        self._closing.add(thread_id)
        try:
            yield
        finally:
            self._closing.discard(thread_id)

    def start(
        self,
        thread_id: str,
        first: RunFrame,
        frames: AsyncGenerator[Frame, None],
        *,
        on_cancel: Callable[[], Awaitable[None]],
        on_end: Callable[[RunResult], object],
    ) -> Run:
        """Start a run on an idle thread; see ``Run`` for the parameters.

        Raises
        ------
        RunActiveError
            If a run is active on the thread.
        """
        self.ensure_idle(thread_id)
        run = Run(
            first,
            frames,
            on_cancel=on_cancel,
            on_end=on_end,
            on_close=lambda: self._runs.pop(thread_id, None),
        )
        self._runs[thread_id] = run
        return run

    async def stop(self, thread_id: str) -> bool:
        """Stop the active run of the thread and return whether there was one."""
        run = self._runs.get(thread_id)
        if run is None:
            return False
        await run.stop()
        return True
