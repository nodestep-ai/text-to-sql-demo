import json

import pytest
from pydantic import TypeAdapter, ValidationError

from samples import PROPOSALS, SAMPLES
from text_to_sql_demo.charts import Axis, ChartSpec
from text_to_sql_demo.execution import SqlResult
from text_to_sql_demo.frames import (
    ChartFrame,
    ClarificationFrame,
    DoneFrame,
    FinalFrame,
    Frame,
    Proposal,
    SqlResultFrame,
    StoppedFrame,
    SuggestionsFrame,
    TokenFrame,
    ToolFrame,
    encode_frame,
)

CONTRACT = {
    "run": {"thread_id", "run_id", "database"},
    "token": {"text"},
    "tool": {"call_id", "name", "status", "arguments", "summary"},
    "sql_result": {
        "call_id",
        "sql",
        "columns",
        "rows",
        "row_count",
        "truncated",
        "elapsed_ms",
    },
    "chart": {"call_id", "kind", "title", "x", "y", "data"},
    "clarification": {"key", "question", "proposals", "allow_free_text"},
    "final": {"text"},
    "suggestions": {"questions"},
    "usage": {"model", "input_tokens", "output_tokens"},
    "error": {"kind", "message"},
    "stopped": set(),
    "done": set(),
}


def split(message: str) -> tuple[str, str]:
    assert message.endswith("\n\n")
    event_line, data_line = message.removesuffix("\n\n").split("\n")
    return event_line.removeprefix("event: "), data_line.removeprefix("data: ")


def test_samples_cover_every_contract_type():
    assert [frame.type for frame in SAMPLES] == list(CONTRACT)


@pytest.mark.parametrize("frame", SAMPLES, ids=lambda frame: frame.type)
def test_data_keys_match_the_contract(frame: Frame):
    event, data = split(encode_frame(frame))
    assert event == frame.type
    assert set(json.loads(data)) == CONTRACT[frame.type]


def test_exact_wire_format():
    assert (
        encode_frame(TokenFrame(text="Hi")) == 'event: token\ndata: {"text":"Hi"}\n\n'
    )
    assert encode_frame(DoneFrame()) == "event: done\ndata: {}\n\n"
    assert encode_frame(StoppedFrame()) == "event: stopped\ndata: {}\n\n"


def test_clarification_sends_its_proposals():
    frame = ClarificationFrame(
        key="clarify", question="Which?", proposals=PROPOSALS, allow_free_text=False
    )
    _, data = split(encode_frame(frame))
    assert json.loads(data) == {
        "key": "clarify",
        "question": "Which?",
        "proposals": [
            {
                "id": "gross",
                "label": "Gross revenue",
                "description": "Sum of completed order items.",
            },
            {
                "id": "net",
                "label": "Net revenue",
                "description": "Gross revenue minus refunds.",
            },
        ],
        "allow_free_text": False,
    }


def proposal(proposal_id: str) -> Proposal:
    return Proposal(id=proposal_id, label=proposal_id.title(), description="d")


@pytest.mark.parametrize(
    "ids",
    [["a"], ["a", "b", "c", "d", "e"], ["a", "a"]],
    ids=["one", "five", "duplicate"],
)
def test_clarification_needs_two_to_four_distinct_proposals(ids: list[str]):
    with pytest.raises(ValidationError):
        ClarificationFrame(
            key="k",
            question="q",
            proposals=[proposal(i) for i in ids],
            allow_free_text=True,
        )


def test_clarification_takes_four_proposals():
    frame = ClarificationFrame(
        key="k",
        question="q",
        proposals=[proposal(i) for i in "abcd"],
        allow_free_text=True,
    )
    assert [p.id for p in frame.proposals] == ["a", "b", "c", "d"]


@pytest.mark.parametrize("count", [0, 1, 4])
def test_suggestions_need_two_or_three_questions(count: int):
    with pytest.raises(ValidationError):
        SuggestionsFrame(questions=[f"q{n}" for n in range(count)])


def test_suggestions_frame():
    _, data = split(encode_frame(SuggestionsFrame(questions=["a?", "b?", "c?"])))
    assert json.loads(data) == {"questions": ["a?", "b?", "c?"]}


def test_newlines_stay_inside_the_json():
    _, data = split(encode_frame(FinalFrame(text="one\ntwo\r\nthree")))
    assert json.loads(data) == {"text": "one\ntwo\r\nthree"}


def test_tool_frame_keeps_null_fields():
    frame = ToolFrame(call_id="c", name="list_tables", status="finished")
    _, data = split(encode_frame(frame))
    assert json.loads(data) == {
        "call_id": "c",
        "name": "list_tables",
        "status": "finished",
        "arguments": None,
        "summary": None,
    }


def test_sql_result_frame_from_result():
    result = SqlResult(
        sql="SELECT 1",
        columns=["1"],
        rows=[[1]],
        row_count=1,
        truncated=False,
        elapsed_ms=0,
    )
    frame = SqlResultFrame.from_result("c1", result)
    assert frame.call_id == "c1"
    assert frame.rows == [[1]]


def test_chart_frame_from_spec():
    spec = ChartSpec(
        kind="line",
        title="Sales",
        x=Axis(field="year", label="Year"),
        y=Axis(field="total", label="Total"),
        data=[{"year": 2024, "total": 1.5}],
    )
    frame = ChartFrame.from_spec("c2", spec)
    _, data = split(encode_frame(frame))
    assert json.loads(data) == {"call_id": "c2", **spec.model_dump()}


def test_chart_kind_is_bar_or_line():
    with pytest.raises(ValidationError):
        ChartSpec.model_validate(
            {
                "kind": "pie",
                "title": "t",
                "x": {"field": "a", "label": "A"},
                "y": {"field": "b", "label": "B"},
                "data": [],
            }
        )


def test_frames_parse_back_by_type():
    adapter = TypeAdapter(list[Frame])
    dumped = adapter.dump_python(SAMPLES, mode="json")
    assert all("type" in item for item in dumped)
    assert adapter.validate_python(dumped) == SAMPLES
