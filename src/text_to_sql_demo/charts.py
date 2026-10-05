from typing import Literal

from pydantic import BaseModel, JsonValue


class Axis(BaseModel):
    """One chart axis: the data field it reads and its label."""

    field: str
    label: str


class ChartSpec(BaseModel):
    """A bar or line chart, sent to the browser as data.

    ``x.field`` and ``y.field`` are keys of the rows in ``data``, which are
    the column names of the ``run_sql`` result (``SqlResult.columns``). A
    result column without an alias is named after the formatted SQL, for
    example ``COUNT(*)``, not after the text the model wrote.
    """

    kind: Literal["bar", "line"]
    title: str
    x: Axis
    y: Axis
    data: list[dict[str, JsonValue]]
