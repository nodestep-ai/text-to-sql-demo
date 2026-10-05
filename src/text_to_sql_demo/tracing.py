import nodeartifact
from nodestep import AgentState, Graph

OPENAI_PROVIDER = "openai"
SCRIPTED_PROVIDER = "scripted"
SCHEDULE_DELAY_MILLIS = 500


class Tracing:
    """Sends the traces of the agent graph to a nodeartifact server.

    Sets up an OpenTelemetry tracer provider that exports to the nodeartifact
    server (OTLP/HTTP) at ``endpoint``, as service ``text-to-sql-demo``. Spans are
    sent in batches every ``SCHEDULE_DELAY_MILLIS`` milliseconds, so that
    nodeartifact's live mode follows a run closely; call ``shutdown`` before the
    process exits.

    Parameters
    ----------
    endpoint
        The address of ``nodeartifact serve``, for example ``http://127.0.0.1:4318``.
    provider_name
        The ``gen_ai.provider.name`` of the model spans: ``"openai"`` for
        ``OpenAIChat``, ``"scripted"`` for the demo's scripted model.
    """

    def __init__(self, endpoint: str, *, provider_name: str) -> None:
        self._provider = nodeartifact.configure(
            endpoint,
            service_name="text-to-sql-demo",
            schedule_delay_millis=SCHEDULE_DELAY_MILLIS,
        )
        self._provider_name = provider_name

    def instrument(self, graph: Graph[AgentState]) -> Graph[AgentState]:
        """Return a copy of ``graph`` with nodeartifact's tracing middleware.

        Parameters
        ----------
        graph
            The agent graph; it is not changed.

        Returns
        -------
        Graph[AgentState]
        """
        return nodeartifact.instrument(
            graph, tracer_provider=self._provider, provider_name=self._provider_name
        )

    def shutdown(self) -> None:
        """Send the spans not sent yet and stop the exporter."""
        self._provider.shutdown()
