from text_to_sql_demo.charts import Axis
from text_to_sql_demo.frames import (
    ChartFrame,
    ClarificationFrame,
    DoneFrame,
    ErrorFrame,
    FinalFrame,
    Frame,
    Proposal,
    RunFrame,
    SqlResultFrame,
    StoppedFrame,
    SuggestionsFrame,
    TokenFrame,
    ToolFrame,
    UsageFrame,
)

PROPOSALS = [
    Proposal(
        id="gross", label="Gross revenue", description="Sum of completed order items."
    ),
    Proposal(id="net", label="Net revenue", description="Gross revenue minus refunds."),
]

SAMPLES: list[Frame] = [
    RunFrame(thread_id="t1", run_id="r1", database="demo_shop"),
    TokenFrame(text="There are "),
    ToolFrame(
        call_id="c1",
        name="run_sql",
        status="started",
        arguments={"sql": "SELECT 1"},
        summary=None,
    ),
    SqlResultFrame(
        call_id="c1",
        sql="SELECT 1",
        columns=["1"],
        rows=[[1]],
        row_count=1,
        truncated=False,
        elapsed_ms=2,
    ),
    ChartFrame(
        call_id="c2",
        kind="bar",
        title="Units per product",
        x=Axis(field="product", label="Product"),
        y=Axis(field="units", label="Units"),
        data=[{"product": "Espresso beans", "units": 12}],
    ),
    ClarificationFrame(
        key="clarify",
        question="Which revenue?",
        proposals=PROPOSALS,
        allow_free_text=True,
    ),
    FinalFrame(text="There are 3 stores."),
    SuggestionsFrame(
        questions=["Which store has most returns?", "Which product sells best?"]
    ),
    UsageFrame(model="gpt-6-luna", input_tokens=120, output_tokens=30),
    ErrorFrame(kind="agent_error", message="boom"),
    StoppedFrame(),
    DoneFrame(),
]
