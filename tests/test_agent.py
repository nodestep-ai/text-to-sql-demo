import asyncio
from collections.abc import AsyncIterator

import pytest

from text_to_sql_demo.agent import (
    AgentCall,
    ChatAgent,
    Delay,
    ScriptedAgent,
)
from text_to_sql_demo.errors import ScriptExhaustedError
from text_to_sql_demo.frames import FinalFrame, Frame, TokenFrame

WHERE = {"thread_id": "t1", "database": "demo_shop", "branch": "main"}


async def collect(frames: AsyncIterator[Frame]) -> list[Frame]:
    return [frame async for frame in frames]


def test_the_scripted_agent_satisfies_the_protocol():
    agent: ChatAgent = ScriptedAgent()
    assert agent is not None


async def test_scripts_play_in_call_order():
    agent = ScriptedAgent(
        [
            [TokenFrame(text="Hi"), FinalFrame(text="Hi")],
            [FinalFrame(text="Gross it is")],
            [FinalFrame(text="Edited")],
        ]
    )
    first = await collect(agent.run(**WHERE, turn_id="u1", message="hello"))
    second = await collect(
        agent.resume(**WHERE, turn_id="u2", key="clarify", answer="gross")
    )
    third = await collect(
        agent.edit(
            thread_id="t1",
            database="demo_shop",
            branch="b2",
            turn_id="u1",
            message="hello again",
        )
    )
    assert first == [TokenFrame(text="Hi"), FinalFrame(text="Hi")]
    assert second == [FinalFrame(text="Gross it is")]
    assert third == [FinalFrame(text="Edited")]
    assert agent.calls == [
        AgentCall(method="run", **WHERE, turn_id="u1", message="hello"),
        AgentCall(
            method="resume", **WHERE, turn_id="u2", key="clarify", answer="gross"
        ),
        AgentCall(
            method="edit",
            thread_id="t1",
            database="demo_shop",
            branch="b2",
            turn_id="u1",
            message="hello again",
        ),
    ]


async def test_cancel_is_recorded_and_uses_no_script():
    agent = ScriptedAgent([[FinalFrame(text="next")]])
    await agent.cancel(**WHERE)
    assert agent.calls == [AgentCall(method="cancel", **WHERE)]
    assert await collect(agent.run(**WHERE, turn_id="u", message="m")) == [
        FinalFrame(text="next")
    ]


async def test_exception_is_raised_after_the_frames_before_it():
    agent = ScriptedAgent([[TokenFrame(text="a"), RuntimeError("model down")]])
    frames = agent.run(**WHERE, turn_id="u", message="m")
    assert await anext(frames) == TokenFrame(text="a")
    with pytest.raises(RuntimeError, match="model down"):
        await anext(frames)


async def test_running_out_of_scripts_raises():
    agent = ScriptedAgent()
    with pytest.raises(ScriptExhaustedError, match="no script left for call 1"):
        await collect(agent.run(**WHERE, turn_id="u", message="m"))


async def test_a_delay_waits_between_frames(monkeypatch: pytest.MonkeyPatch):
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    agent = ScriptedAgent(
        [[TokenFrame(text="a"), Delay(seconds=0.25), TokenFrame(text="b")]]
    )
    frames = await collect(agent.run(**WHERE, turn_id="u", message="m"))
    assert frames == [TokenFrame(text="a"), TokenFrame(text="b")]
    assert waited == [0.25]


def test_a_delay_is_not_negative():
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        Delay(seconds=-1)


async def test_a_long_delay_ends_only_when_cancelled():
    agent = ScriptedAgent([[TokenFrame(text="a"), Delay(seconds=3600)]])
    received: list[Frame] = []
    first = asyncio.Event()

    async def consume() -> None:
        async for frame in agent.run(**WHERE, turn_id="u", message="m"):
            received.append(frame)
            first.set()

    task = asyncio.create_task(consume())
    await first.wait()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert received == [TokenFrame(text="a")]
