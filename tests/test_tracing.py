from pathlib import Path

import nodeartifact
import pytest
from nodestep import AgentState, Graph, ScriptedChat
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from harness import RoutingChat, calls, tool_call
from text_to_sql_demo.frames import FinalFrame
from text_to_sql_demo.nodestep_agent import NodestepAgent
from text_to_sql_demo.settings import ServerSettings

ENDPOINT = "http://127.0.0.1:4318"


def settings_for(data_dir: Path, endpoint: str | None) -> ServerSettings:
    return ServerSettings.model_validate(
        {
            "TEXT_TO_SQL_DEMO_DATA_DIR": str(data_dir),
            "TEXT_TO_SQL_DEMO_WEB_DIST": str(data_dir / "no-dist"),
            "TEXT_TO_SQL_DEMO_OTLP_ENDPOINT": endpoint or "",
        }
    )


async def answer(agent: NodestepAgent) -> str:
    frames = [
        frame
        async for frame in agent.run(
            thread_id="t1",
            database="fixture",
            branch="main",
            turn_id="u1",
            message="Tables?",
        )
    ]
    return next(frame.text for frame in frames if isinstance(frame, FinalFrame))


def script() -> ScriptedChat:
    return ScriptedChat([calls(tool_call("list_tables", {}, "c1")), "Three tables."])


@pytest.mark.usefixtures("database_path")
async def test_without_an_endpoint_nothing_is_traced(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("tracing was set up without an endpoint")

    monkeypatch.setattr(nodeartifact, "configure", refuse)
    monkeypatch.setattr(nodeartifact, "instrument", refuse)
    agent = NodestepAgent.from_settings(
        script(),
        settings_for(data_dir, None),
        model="gpt-6-luna",
        provider_name="openai",
    )
    assert await answer(agent) == "Three tables."
    agent.close()


@pytest.mark.usefixtures("database_path")
async def test_with_an_endpoint_the_graph_is_instrumented(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    provider = TracerProvider()
    configured: list[tuple[str, str, float | None]] = []
    instrumented: list[tuple[str, TracerProvider | None, str]] = []

    def configure(
        endpoint: str, *, service_name: str, schedule_delay_millis: float | None = None
    ) -> TracerProvider:
        configured.append((endpoint, service_name, schedule_delay_millis))
        return provider

    def instrument(
        graph: Graph[AgentState],
        *,
        tracer_provider: TracerProvider | None = None,
        provider_name: str,
    ) -> Graph[AgentState]:
        instrumented.append((graph.name, tracer_provider, provider_name))
        return graph

    monkeypatch.setattr(nodeartifact, "configure", configure)
    monkeypatch.setattr(nodeartifact, "instrument", instrument)
    agent = NodestepAgent.from_settings(
        script(),
        settings_for(data_dir, ENDPOINT),
        model="gpt-6-luna",
        provider_name="openai",
    )
    assert configured == [(ENDPOINT, "text-to-sql-demo", 500)]
    assert instrumented == [
        ("text-to-sql-demo-analyst", provider, "openai"),
        ("text-to-sql-demo", provider, "openai"),
    ]
    assert await answer(agent) == "Three tables."


@pytest.mark.usefixtures("database_path")
async def test_spans_of_a_run_reach_the_exporter(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def configure(
        endpoint: str, *, service_name: str, schedule_delay_millis: float
    ) -> TracerProvider:
        return provider

    monkeypatch.setattr(nodeartifact, "configure", configure)
    agent = NodestepAgent.from_settings(
        script(),
        settings_for(data_dir, ENDPOINT),
        model="gpt-6-luna",
        provider_name="openai",
    )
    assert await answer(agent) == "Three tables."
    names = [span.name for span in exporter.get_finished_spans()]
    assert "nodestep.graph text-to-sql-demo" in names
    assert "nodestep.node think" in names
    assert "nodestep.node act" in names
    assert "execute_tool list_tables" in names
    assert any(name.startswith("chat ") for name in names)


@pytest.mark.usefixtures("database_path")
async def test_model_spans_name_the_provider_given(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def configure(
        endpoint: str, *, service_name: str, schedule_delay_millis: float
    ) -> TracerProvider:
        return provider

    monkeypatch.setattr(nodeartifact, "configure", configure)
    agent = NodestepAgent.from_settings(
        script(),
        settings_for(data_dir, ENDPOINT),
        model="scripted",
        provider_name="scripted",
    )
    assert await answer(agent) == "Three tables."
    providers = {
        span.attributes.get("gen_ai.provider.name")
        for span in exporter.get_finished_spans()
        if span.name.startswith("chat ") and span.attributes is not None
    }
    assert providers == {"scripted"}


@pytest.mark.usefixtures("database_path")
async def test_closing_the_agent_sends_the_spans_not_sent_yet(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(exporter, schedule_delay_millis=600_000)
    )

    def configure(
        endpoint: str, *, service_name: str, schedule_delay_millis: float
    ) -> TracerProvider:
        return provider

    monkeypatch.setattr(nodeartifact, "configure", configure)
    agent = NodestepAgent.from_settings(
        script(),
        settings_for(data_dir, ENDPOINT),
        model="gpt-6-luna",
        provider_name="openai",
    )
    assert await answer(agent) == "Three tables."
    assert exporter.get_finished_spans() == ()
    agent.close()
    names = [span.name for span in exporter.get_finished_spans()]
    assert "nodestep.graph text-to-sql-demo" in names


@pytest.mark.usefixtures("database_path")
async def test_each_sub_agent_is_its_own_run_in_the_trace(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def configure(
        endpoint: str, *, service_name: str, schedule_delay_millis: float
    ) -> TracerProvider:
        return provider

    monkeypatch.setattr(nodeartifact, "configure", configure)
    topics = [
        {"name": "teachers", "question": "Teachers?"},
        {"name": "courses", "question": "Courses?"},
    ]
    chat = RoutingChat(
        ScriptedChat(
            [calls(tool_call("analyze_topics", {"topics": topics}, "c1")), "Done."]
        ),
        {
            "Teachers?": ScriptedChat(["3 teachers."]),
            "Courses?": ScriptedChat(["3 courses."]),
        },
    )
    agent = NodestepAgent.from_settings(
        chat,
        settings_for(data_dir, ENDPOINT),
        model="scripted",
        provider_name="scripted",
    )
    assert await answer(agent) == "Done."
    spans = exporter.get_finished_spans()
    [main] = [span for span in spans if span.name == "nodestep.graph text-to-sql-demo"]
    [delegate] = [span for span in spans if span.name == "nodestep.node delegate"]
    runs = [
        span for span in spans if span.name == "nodestep.graph text-to-sql-demo-analyst"
    ]
    assert len(runs) == 2
    assert main.context is not None
    assert delegate.context is not None
    for run in runs:
        assert run.context is not None
        assert run.context.trace_id == main.context.trace_id
        assert run.parent is not None
        assert run.parent.span_id == delegate.context.span_id
    threads = {str((run.attributes or {}).get("nodestep.thread_id")) for run in runs}
    assert len(threads) == 2
    assert all(thread.startswith("t1:sub:") for thread in threads)
