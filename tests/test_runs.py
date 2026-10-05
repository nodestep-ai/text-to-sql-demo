import asyncio
from collections.abc import AsyncGenerator

import pytest

from samples import SAMPLES
from text_to_sql_demo.agent import Delay
from text_to_sql_demo.errors import RunActiveError
from text_to_sql_demo.frames import (
    DoneFrame,
    ErrorFrame,
    FinalFrame,
    Frame,
    RunFrame,
    StoppedFrame,
    TokenFrame,
    UsageFrame,
)
from text_to_sql_demo.runs import Run, RunRegistry, RunResult

FIRST = RunFrame(thread_id="t1", run_id="r1", database="demo_shop")
CLARIFICATION = SAMPLES[5]


class Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.results: list[RunResult] = []

    async def cancel(self) -> None:
        self.events.append("cancel")

    def end(self, result: RunResult) -> None:
        self.events.append("end")
        self.results.append(result)


class Stream:
    def __init__(self, steps: list[Frame | Exception | Delay]) -> None:
        self.steps = steps
        self.closed = False

    async def frames(self) -> AsyncGenerator[Frame, None]:
        try:
            for step in self.steps:
                if isinstance(step, Delay):
                    await asyncio.sleep(step.seconds)
                elif isinstance(step, Exception):
                    raise step
                else:
                    yield step
        finally:
            self.closed = True


def start(
    registry: RunRegistry, recorder: Recorder, steps: list[Frame | Exception | Delay]
) -> tuple[Run, Stream]:
    stream = Stream(steps)
    run = registry.start(
        "t1",
        FIRST,
        stream.frames(),
        on_cancel=recorder.cancel,
        on_end=recorder.end,
    )
    return run, stream


async def received(run: Run) -> list[Frame]:
    return [frame async for frame in run.events()]


async def test_frames_pass_through_between_run_and_done():
    recorder = Recorder()
    run, stream = start(
        RunRegistry(),
        recorder,
        [
            TokenFrame(text="There are "),
            SAMPLES[2],
            TokenFrame(text="3."),
            FinalFrame(text="There are 3."),
            UsageFrame(model="m", input_tokens=1, output_tokens=2),
        ],
    )
    frames = await received(run)
    assert frames[0] == FIRST
    assert frames[-1] == DoneFrame()
    assert [frame.type for frame in frames] == [
        "run",
        "token",
        "tool",
        "token",
        "final",
        "usage",
        "done",
    ]
    assert recorder.results == [
        RunResult(
            status="completed",
            text="There are 3.",
            frames=[
                TokenFrame(text="There are "),
                SAMPLES[2],
                UsageFrame(model="m", input_tokens=1, output_tokens=2),
            ],
        )
    ]
    assert stream.closed


async def test_without_final_the_text_is_the_tokens():
    recorder = Recorder()
    run, _ = start(
        RunRegistry(), recorder, [TokenFrame(text="a"), TokenFrame(text="b")]
    )
    await received(run)
    assert recorder.results[0].text == "ab"


async def test_text_before_a_tool_call_is_kept_in_its_place():
    recorder = Recorder()
    run, _ = start(
        RunRegistry(),
        recorder,
        [
            TokenFrame(text="Let me "),
            TokenFrame(text="check."),
            SAMPLES[2],
            SAMPLES[3],
            TokenFrame(text="One "),
            TokenFrame(text="row."),
        ],
    )
    await received(run)
    assert recorder.results == [
        RunResult(
            status="completed",
            text="One row.",
            frames=[TokenFrame(text="Let me check."), SAMPLES[2], SAMPLES[3]],
        )
    ]


async def test_text_before_a_clarification_is_kept_before_it():
    recorder = Recorder()
    run, _ = start(
        RunRegistry(), recorder, [TokenFrame(text="One question first."), CLARIFICATION]
    )
    await received(run)
    assert recorder.results[0].frames == [
        TokenFrame(text="One question first."),
        CLARIFICATION,
    ]
    assert recorder.results[0].text == ""


async def test_a_clarification_makes_the_run_wait():
    recorder = Recorder()
    run, _ = start(RunRegistry(), recorder, [CLARIFICATION, SAMPLES[7]])
    await received(run)
    assert recorder.results[0].status == "waiting"
    assert recorder.results[0].frames == [CLARIFICATION, SAMPLES[7]]


async def test_an_exception_becomes_an_error_frame():
    recorder = Recorder()
    run, stream = start(
        RunRegistry(), recorder, [TokenFrame(text="Part"), RuntimeError("model down")]
    )
    frames = await received(run)
    failure = ErrorFrame(kind="agent_error", message="model down")
    assert frames[1:] == [TokenFrame(text="Part"), failure, DoneFrame()]
    assert recorder.results == [
        RunResult(status="error", text="Part", frames=[failure])
    ]
    assert recorder.events == ["end"]
    assert stream.closed


async def test_an_exception_without_a_message_names_its_type():
    recorder = Recorder()
    run, _ = start(RunRegistry(), recorder, [ValueError()])
    frames = await received(run)
    assert frames[1] == ErrorFrame(kind="agent_error", message="ValueError")


async def test_stopping_a_slow_stream():
    registry = RunRegistry()
    recorder = Recorder()
    run, stream = start(
        registry, recorder, [TokenFrame(text="Part"), Delay(seconds=3600)]
    )
    events = run.events()
    assert await anext(events) == FIRST
    assert await anext(events) == TokenFrame(text="Part")
    assert await registry.stop("t1") is True
    assert [frame async for frame in events] == [StoppedFrame(), DoneFrame()]
    assert recorder.events == ["cancel", "end"]
    assert recorder.results == [RunResult(status="stopped", text="Part", frames=[])]
    assert stream.closed
    assert await registry.stop("t1") is False
    registry.ensure_idle("t1")


async def test_stopping_twice_cancels_once():
    registry = RunRegistry()
    recorder = Recorder()
    run, _ = start(registry, recorder, [Delay(seconds=3600)])
    await asyncio.sleep(0)
    await asyncio.gather(registry.stop("t1"), registry.stop("t1"), run.stop())
    assert recorder.events == ["cancel", "end"]


async def test_stopping_before_the_task_ran():
    registry = RunRegistry()
    recorder = Recorder()
    run, stream = start(registry, recorder, [TokenFrame(text="never")])
    await run.stop()
    assert await received(run) == [FIRST, StoppedFrame(), DoneFrame()]
    assert recorder.results == [RunResult(status="stopped", text="", frames=[])]
    assert recorder.events == ["cancel", "end"]
    assert not stream.closed
    registry.ensure_idle("t1")


async def test_an_error_frame_makes_the_status_error():
    recorder = Recorder()
    failure = ErrorFrame(kind="agent_unavailable", message="No agent.")
    run, _ = start(RunRegistry(), recorder, [TokenFrame(text="Part"), failure])
    frames = await received(run)
    assert frames[1:] == [TokenFrame(text="Part"), failure, DoneFrame()]
    assert recorder.results == [
        RunResult(status="error", text="Part", frames=[failure])
    ]


async def test_an_error_after_a_clarification_is_an_error():
    recorder = Recorder()
    failure = ErrorFrame(kind="agent_error", message="boom")
    run, _ = start(RunRegistry(), recorder, [CLARIFICATION, failure])
    await received(run)
    assert recorder.results[0].status == "error"


async def test_stopping_a_finished_run_changes_nothing():
    recorder = Recorder()
    run, _ = start(RunRegistry(), recorder, [FinalFrame(text="done")])
    await received(run)
    await run.stop()
    assert recorder.results[0].status == "completed"
    assert recorder.events == ["end"]


async def test_one_run_per_thread():
    registry = RunRegistry()
    recorder = Recorder()
    run, _ = start(registry, recorder, [Delay(seconds=3600)])
    with pytest.raises(RunActiveError, match="t1"):
        start(registry, recorder, [])
    with pytest.raises(RunActiveError):
        registry.ensure_idle("t1")
    other = registry.start(
        "t2",
        FIRST,
        Stream([]).frames(),
        on_cancel=recorder.cancel,
        on_end=recorder.end,
    )
    await received(other)
    await run.stop()
    registry.ensure_idle("t1")
    registry.ensure_idle("t2")


async def test_a_failing_recorder_still_ends_the_stream():
    registry = RunRegistry()

    def broken(result: RunResult) -> None:
        raise OSError("disk full")

    async def cancel() -> None:
        pass

    run = registry.start(
        "t1", FIRST, Stream([]).frames(), on_cancel=cancel, on_end=broken
    )
    assert await received(run) == [FIRST, DoneFrame()]
    with pytest.raises(OSError, match="disk full"):
        await run.wait()
    registry.ensure_idle("t1")


async def test_a_thread_is_busy_while_its_question_is_closed():
    registry = RunRegistry()
    async with registry.closing("t1"):
        with pytest.raises(RunActiveError, match="closing its open question"):
            registry.ensure_idle("t1")
        with pytest.raises(RunActiveError):
            start(registry, Recorder(), [])
        registry.ensure_idle("t2")
    registry.ensure_idle("t1")


async def test_closing_needs_an_idle_thread():
    registry = RunRegistry()
    run, _ = start(registry, Recorder(), [Delay(seconds=3600)])
    with pytest.raises(RunActiveError, match="still answering"):
        async with registry.closing("t1"):
            pass
    await run.stop()
