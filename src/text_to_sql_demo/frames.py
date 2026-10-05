from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, JsonValue, model_validator

from text_to_sql_demo.charts import ChartSpec
from text_to_sql_demo.execution import SqlResult


class RunFrame(BaseModel):
    """First frame of every stream: the thread, the run and the thread's database."""

    type: Literal["run"] = "run"
    thread_id: str
    run_id: str
    database: str


class TokenFrame(BaseModel):
    """A piece of streamed answer text."""

    type: Literal["token"] = "token"
    text: str


class ToolFrame(BaseModel):
    """A tool call starting, finishing or failing."""

    type: Literal["tool"] = "tool"
    call_id: str
    name: str
    status: Literal["started", "finished", "error"]
    arguments: dict[str, JsonValue] | None = None
    summary: str | None = None


class SqlResultFrame(SqlResult):
    """The result of a ``run_sql`` tool call."""

    type: Literal["sql_result"] = "sql_result"
    call_id: str

    @classmethod
    def from_result(cls, call_id: str, result: SqlResult) -> Self:
        """Build the frame for ``result`` of tool call ``call_id``."""
        return cls(call_id=call_id, **result.model_dump())


class ChartFrame(ChartSpec):
    """A chart made by a ``make_chart`` tool call."""

    type: Literal["chart"] = "chart"
    call_id: str

    @classmethod
    def from_spec(cls, call_id: str, spec: ChartSpec) -> Self:
        """Build the frame for ``spec`` of tool call ``call_id``."""
        return cls(call_id=call_id, **spec.model_dump())


class Proposal(BaseModel):
    """One answer the user can pick for a clarification.

    ``description`` says in one line what the answer would compute.
    """

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str


class ClarificationFrame(BaseModel):
    """A question for the user; the run pauses until it is answered.

    The answer is the ``id`` of one of the 2 to 4 ``proposals``, or free text
    when ``allow_free_text`` is true.
    """

    type: Literal["clarification"] = "clarification"
    key: str
    question: str
    proposals: list[Proposal] = Field(min_length=2, max_length=4)
    allow_free_text: bool

    @model_validator(mode="after")
    def _distinct_ids(self) -> Self:
        ids = [proposal.id for proposal in self.proposals]
        if len(set(ids)) != len(ids):
            raise ValueError(f"proposal ids must be distinct, got {ids}")
        return self

    def proposal(self, proposal_id: str) -> Proposal | None:
        """Return the proposal with ``proposal_id``, or ``None``."""
        return next((p for p in self.proposals if p.id == proposal_id), None)


class FinalFrame(BaseModel):
    """The complete answer text."""

    type: Literal["final"] = "final"
    text: str


class SuggestionsFrame(BaseModel):
    """Two or three follow-up questions, sent after ``final``."""

    type: Literal["suggestions"] = "suggestions"
    questions: list[str] = Field(min_length=2, max_length=3)


class UsageFrame(BaseModel):
    """Token usage of one model call."""

    type: Literal["usage"] = "usage"
    model: str
    input_tokens: int
    output_tokens: int


class ErrorFrame(BaseModel):
    """An error that ended the run."""

    type: Literal["error"] = "error"
    kind: str
    message: str


class StoppedFrame(BaseModel):
    """The run was stopped; sent before ``done`` while the stream is open."""

    type: Literal["stopped"] = "stopped"


class DoneFrame(BaseModel):
    """Last frame of every stream."""

    type: Literal["done"] = "done"


type Frame = Annotated[
    RunFrame
    | TokenFrame
    | ToolFrame
    | SqlResultFrame
    | ChartFrame
    | ClarificationFrame
    | FinalFrame
    | SuggestionsFrame
    | UsageFrame
    | ErrorFrame
    | StoppedFrame
    | DoneFrame,
    Field(discriminator="type"),
]


def encode_frame(frame: Frame) -> str:
    """Encode one frame as a server-sent event.

    Parameters
    ----------
    frame
        Any frame.

    Returns
    -------
    str
        ``"event: <type>\\ndata: <json>\\n\\n"``; the JSON holds every field
        except ``type``.
    """
    data = frame.model_dump_json(exclude={"type"})
    return f"event: {frame.type}\ndata: {data}\n\n"
