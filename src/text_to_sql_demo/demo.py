import asyncio
import hashlib
import re
import shutil
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Literal

from nodestep import ScriptedChat
from nodestep.chat import (
    AIMessage,
    ChatRequest,
    ChatResponse,
    ChatStreamChunk,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from nodestep.middleware import (
    Memory,
    MemoryDeleteResult,
    MemoryListResult,
    MemorySearchResult,
)
from pydantic import BaseModel, JsonValue, ValidationError

from text_to_sql_demo.database import Database
from text_to_sql_demo.execution import SqlResult, SqlValue
from text_to_sql_demo.frames import Proposal
from text_to_sql_demo.memory import CONTENT_LENGTH, KEY_LENGTH, TITLE_LENGTH
from text_to_sql_demo.subagents import ANALYST_PROMPT, Topic, TopicFindings
from text_to_sql_demo.tools import ClarificationAnswer, TableList, question_key

DEMO_DATABASE = "demo_shop"

TOP_PRODUCTS = "Which ten products sold the most units?"
REVENUE_PER_MONTH = "What is the revenue per month?"
RETURNS_PER_STORE = "Which store has the most returns?"
CATEGORY_REVENUE = "Which category brings in the most revenue?"
REPORT = "Write a report on sales by store in 2025."
QUESTIONS = (
    TOP_PRODUCTS,
    REVENUE_PER_MONTH,
    RETURNS_PER_STORE,
    CATEGORY_REVENUE,
    REPORT,
)

ITEM_REVENUE = "i.quantity * i.unit_price * (1 - i.discount)"
COMPLETED_ITEMS = (
    "FROM order_items AS i JOIN orders AS o ON o.order_id = i.order_id "
    "JOIN products AS p ON p.product_id = i.product_id "
    "JOIN categories AS c ON c.category_id = p.category_id "
    "WHERE o.status = 'completed'"
)
TOP_PRODUCTS_SQL = (
    "SELECT p.name AS product, sum(i.quantity) AS units "
    "FROM order_items AS i JOIN orders AS o ON o.order_id = i.order_id "
    "JOIN products AS p ON p.product_id = i.product_id "
    "WHERE o.status = 'completed' "
    "GROUP BY p.product_id ORDER BY units DESC, p.name LIMIT 10"
)
REVENUE_PER_MONTH_SQL = (
    "SELECT strftime('%Y-%m', o.ordered_at) AS month, "
    f"round(sum({ITEM_REVENUE}), 2) AS revenue "
    "FROM orders AS o JOIN order_items AS i ON i.order_id = o.order_id "
    "WHERE o.status = 'completed' GROUP BY month ORDER BY month"
)
RETURNS_FIRST_TRY_SQL = (
    "SELECT s.name AS store, count(*) AS returns FROM returns AS r "
    "JOIN orders AS o ON o.order_id = r.order_id "
    "LEFT JOIN stores AS s ON s.store_id = o.store_id "
    "GROUP BY s.name ORDER BY returns DESC"
)
RETURNS_SQL = (
    "SELECT coalesce(s.name, 'Online shop') AS store, count(*) AS returns "
    "FROM returns AS r JOIN order_items AS i ON i.order_item_id = r.order_item_id "
    "JOIN orders AS o ON o.order_id = i.order_id "
    "LEFT JOIN stores AS s ON s.store_id = o.store_id "
    "GROUP BY store ORDER BY returns DESC, store"
)
CATEGORY_SQL = {
    "gross": (
        f"SELECT c.name AS category, round(sum({ITEM_REVENUE}), 2) AS revenue "
        f"{COMPLETED_ITEMS} GROUP BY c.name ORDER BY revenue DESC"
    ),
    "net": (
        "WITH refunds AS (SELECT order_item_id, sum(refund) AS refund "
        "FROM returns GROUP BY order_item_id) "
        f"SELECT c.name AS category, round(sum({ITEM_REVENUE} - "
        "coalesce(f.refund, 0)), 2) AS revenue "
        "FROM order_items AS i JOIN orders AS o ON o.order_id = i.order_id "
        "JOIN products AS p ON p.product_id = i.product_id "
        "JOIN categories AS c ON c.category_id = p.category_id "
        "LEFT JOIN refunds AS f ON f.order_item_id = i.order_item_id "
        "WHERE o.status = 'completed' GROUP BY c.name ORDER BY revenue DESC"
    ),
    "share": (
        f"SELECT c.name AS category, round(100.0 * sum({ITEM_REVENUE}) / "
        "(SELECT sum(t.quantity * t.unit_price * (1 - t.discount)) "
        "FROM order_items AS t JOIN orders AS u ON u.order_id = t.order_id "
        "WHERE u.status = 'completed'), 1) AS share "
        f"{COMPLETED_ITEMS} GROUP BY c.name ORDER BY share DESC"
    ),
}
REPORT_SQL = (
    "SELECT coalesce(s.name, 'Online shop') AS store, "
    "count(DISTINCT o.order_id) AS orders, "
    f"round(sum({ITEM_REVENUE}), 2) AS revenue "
    "FROM orders AS o JOIN order_items AS i ON i.order_id = o.order_id "
    "LEFT JOIN stores AS s ON s.store_id = o.store_id "
    "WHERE o.status = 'completed' AND o.ordered_at >= '2025-01-01' "
    "AND o.ordered_at < '2026-01-01' GROUP BY store ORDER BY revenue DESC"
)
REPORT_RETURNS_SQL = (
    "SELECT coalesce(s.name, 'Online shop') AS store, count(*) AS returns, "
    "round(sum(r.refund), 2) AS refunds "
    "FROM returns AS r JOIN order_items AS i ON i.order_item_id = r.order_item_id "
    "JOIN orders AS o ON o.order_id = i.order_id "
    "LEFT JOIN stores AS s ON s.store_id = o.store_id "
    "WHERE r.returned_on >= '2025-01-01' AND r.returned_on < '2026-01-01' "
    "GROUP BY store ORDER BY returns DESC, store"
)
REPORT_PRODUCTS_SQL = (
    "SELECT p.name AS product, sum(i.quantity) AS units "
    "FROM order_items AS i JOIN orders AS o ON o.order_id = i.order_id "
    "JOIN products AS p ON p.product_id = i.product_id "
    "WHERE o.status = 'completed' AND o.ordered_at >= '2025-01-01' "
    "AND o.ordered_at < '2026-01-01' "
    "GROUP BY p.product_id ORDER BY units DESC, p.name LIMIT 5"
)
REPORT_SKILL = "report"
REPORT_TOPICS = [
    Topic(
        name="revenue",
        question="What were the orders and revenue per store in 2025, counting "
        "completed orders only?",
    ),
    Topic(
        name="returns",
        question="How many items were returned per store in 2025, and how much "
        "was refunded?",
    ),
    Topic(
        name="top products",
        question="Which five products sold the most units in 2025, counting "
        "completed orders only?",
    ),
]
REPORT_PARTS = (
    ("revenue", "Revenue"),
    ("returns", "Returns"),
    ("top products", "Top products"),
)
REPORT_TOPIC_SQL = dict(
    zip(
        (topic.question for topic in REPORT_TOPICS),
        (REPORT_SQL, REPORT_RETURNS_SQL, REPORT_PRODUCTS_SQL),
        strict=True,
    )
)
MEMORY_QUERY = "revenue"
REMEMBER = re.compile(
    r"^\s*(?:please\s+)?remember\b(?:\s+that\b)?[\s:,]*(?P<fact>.*)$",
    re.IGNORECASE | re.DOTALL,
)
FORGET = re.compile(
    r"^\s*(?:please\s+)?(?:forget|do\s+not\s+remember|don['\u2019]t\s+remember)\b"
    r"(?:\s+that\b)?[\s:,]*(?P<fact>.*)$",
    re.IGNORECASE | re.DOTALL,
)
LAST_MEMORY = ("", "it", "this", "that")
COMMON_WORD = re.compile(
    r"a|an|and|are|at|be|by|for|from|in|is|it|of|on|or|that|the|this|to|was|with"
)
REMEMBER_HINT = (
    "Tell me what to remember, for example: Remember that revenue counts "
    "completed orders only."
)
FORGET_HINT = (
    "Tell me what to forget, for example: Forget that revenue counts completed "
    "orders only."
)
TOO_LONG = (
    "That is too long to remember: a memory holds at most "
    f"{CONTENT_LENGTH:,} characters."
)
PROPOSALS = [
    Proposal(
        id="gross",
        label="Gross revenue",
        description="Sum of quantity times price after discount, completed orders.",
    ),
    Proposal(
        id="net",
        label="Net of refunds",
        description="Gross revenue minus the refunds paid for returned items.",
    ),
    Proposal(
        id="share",
        label="Share of total",
        description="Each category's gross revenue as a percentage of all revenue.",
    ),
]
NEGATION_FILLERS = (
    "counting",
    "including",
    "subtracting",
    "deducting",
    "taking",
    "the",
)
READING_HINTS = re.compile(
    r"(?P<negation>\b(?:not|before|ignoring)\s+"
    rf"(?:(?:{'|'.join(NEGATION_FILLERS)})\s+)?)?"
    r"(?:(?P<net>\bnet\b|\brefund|\breturn)"
    r"|(?P<share>\bshare|\bpercent|%)"
    r"|(?P<gross>\bgross\b))",
    re.IGNORECASE,
)
READINGS = {
    "gross": "gross revenue",
    "net": "revenue net of refunds",
    "share": "each category's share of the total",
}
MONTHS_EXCEPT_MAY = (
    "january|february|march|april|june|july|august|september|october|november|december"
)
MAY_PREPOSITIONS = ("in", "of", "for", "by", "since", "until", "from", "during")
MAY = (
    "(?:"
    + "|".join(rf"(?<=\b{word} )" for word in MAY_PREPOSITIONS)
    + r")may\b|\bmay(?= \d)"
)
CONSTRAINTS = re.compile(
    r"\b(?:19|20)\d{2}\b"
    r"|\bq[1-4]\b"
    rf"|\b(?:{MONTHS_EXCEPT_MAY})\b|{MAY}"
    r"|\b(?:last|this|past|previous|current) (?:year|quarter|month|week)\b"
    r"|\byear to date\b|\bytd\b"
    r"|\bonline\b|\bin[- ]store\b",
    re.IGNORECASE,
)


def reading_basis(text: str) -> str | None:
    """Return the proposal id that ``text`` names first and does not negate.

    Hints are whole words or word beginnings, so "internet" is not "net".
    A hint right after "not", "before" or "ignoring", also with a word such
    as "counting" or "the" between, is negated, so "gross, not net" is gross. When
    every hint is negated, the reading is the first of gross, net and share
    that is not, so "not counting refunds" is gross.

    Parameters
    ----------
    text
        A free-text answer.

    Returns
    -------
    str | None
        ``"gross"``, ``"net"`` or ``"share"``, or ``None`` when no reading is
        named.
    """
    negated: set[str] = set()
    for match in READING_HINTS.finditer(text):
        key = next(key for key in ("net", "share", "gross") if match[key])
        if match["negation"] is None:
            return key
        negated.add(key)
    if not negated:
        return None
    return next((key for key in ("gross", "net", "share") if key not in negated), None)


def unapplied_constraints(text: str) -> list[str]:
    """Return the dates, periods and sales channels named in ``text``.

    The demo's category query has no filters, so it cannot apply any of
    them. Each is returned once, as written, in the order of the text.

    Parameters
    ----------
    text
        A free-text answer.

    Returns
    -------
    list[str]
    """
    found: dict[str, str] = {}
    for match in CONSTRAINTS.finditer(text):
        found.setdefault(match.group().casefold(), match.group())
    return list(found.values())


def unapplied_note(constraints: list[str], scope: str) -> str:
    """Return the sentence that names ``constraints``, or ``""`` without any.

    Parameters
    ----------
    constraints
        What the question or answer asked for and the demo's query cannot
        apply, as ``unapplied_constraints`` returns it.
    scope
        What the figures cover instead, such as "all completed orders".

    Returns
    -------
    str
    """
    if not constraints:
        return ""
    return (
        f"I could not apply {quoted_list(constraints)} in this demo, so the "
        f"figures cover {scope}. "
    )


def quoted_list(items: list[str]) -> str:
    """Return ``items`` quoted and joined: ``"a", "b" and "c"``.

    Parameters
    ----------
    items
        At least one item.

    Returns
    -------
    str
    """
    quoted = [f'"{item}"' for item in items]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def sentence(text: str) -> str:
    """Return ``text`` on one line with a capital first letter and a full stop.

    Parameters
    ----------
    text
        Any text.

    Returns
    -------
    str
    """
    words = " ".join(text.split()).rstrip(" .!")
    if not words:
        return ""
    return f"{words[0].upper()}{words[1:]}."


def inline(text: str) -> str:
    """Return a sentence to quote after a colon: its first letter in lower case.

    A first word in capitals, such as "VAT", keeps its case.

    Parameters
    ----------
    text
        A sentence.

    Returns
    -------
    str
    """
    if text[1:2].isupper():
        return text
    return text[:1].lower() + text[1:]


def memory_fields(fact: str) -> dict[str, JsonValue]:
    """Return the ``save_memory`` arguments for ``fact``.

    The content is the whole fact as a sentence, the key comes from
    ``memory_key`` and the title is the first eight words, at most
    ``TITLE_LENGTH`` characters.

    Parameters
    ----------
    fact
        What to remember, such as "revenue counts completed orders only".

    Returns
    -------
    dict[str, JsonValue]
        ``key``, ``title`` and ``content``.
    """
    content = sentence(fact)
    title = " ".join(content.rstrip(".").split()[:8])[:TITLE_LENGTH].rstrip()
    return {"key": memory_key(content), "title": title, "content": content}


def memory_key(content: str) -> str:
    """Return the key of a memory: its words in lower case joined by ``-``.

    Only letters a to z and digits count as words. A key that would be
    longer than ``KEY_LENGTH``, or that has no such word, is cut and ends
    with a short hash of the words, so different facts get different keys.

    Parameters
    ----------
    content
        The content of the memory.

    Returns
    -------
    str
    """
    words = "-".join(re.findall(r"[a-z0-9]+", content.lower()))
    if words and len(words) <= KEY_LENGTH:
        return words
    digest = hashlib.sha256((words or content).encode()).hexdigest()[:8]
    stem = words[: KEY_LENGTH - len(digest) - 1].rstrip("-") or "note"
    return f"{stem}-{digest}"


def holds_words(memory: Memory, words: str) -> bool:
    """Return whether every one of ``words`` is a word of the memory's title or content.

    Parameters
    ----------
    memory
        A stored memory.
    words
        Words separated by spaces, as ``search_words`` returns them.

    Returns
    -------
    bool
    """
    held = set(re.findall(r"\w+", memory.searchable_text().lower()))
    return all(word in held for word in words.split())


def search_words(text: str) -> str:
    """Return the words of ``text`` without the most common ones, for a memory search.

    Parameters
    ----------
    text
        What the user asked to forget.

    Returns
    -------
    str
    """
    words = re.findall(r"\w+", text.lower())
    return " ".join(word for word in words if not COMMON_WORD.fullmatch(word))


def euros(value: object) -> str:
    """Return an amount as euros with two decimals: ``1234.5`` gives ``"€1,234.50"``.

    Parameters
    ----------
    value
        A number, or a value whose text is a number.

    Returns
    -------
    str
    """
    return f"€{float(str(value)):,.2f}"


class DemoTurn:
    """The current turn of a request, as the scripted model reads it.

    The turn starts at the last user message. Its id starts every tool-call
    id. The agent sets it to the text-to-sql-demo turn id, and to a new id for each
    edited version, so the ids stay unique across the turns of a thread,
    across the versions of a turn and across a clarification.

    Parameters
    ----------
    request
        The request the model answers.
    """

    def __init__(self, request: ChatRequest) -> None:
        messages = list(request.messages)
        start = max(
            (n for n, m in enumerate(messages) if isinstance(m, HumanMessage)),
            default=-1,
        )
        question = messages[start] if start >= 0 else None
        later = messages[start + 1 :]
        self.message = (question.content or "") if question is not None else ""
        self.asked = {
            question_key(m.content or "")
            for m in messages
            if isinstance(m, HumanMessage)
        }
        self.constraints = unapplied_constraints(self.message)
        self.turn_id = (question.id if question is not None else None) or "turn"
        self.analyst = any(
            isinstance(m, SystemMessage) and m.content == ANALYST_PROMPT
            for m in messages
        )
        replies = [m for m in later if isinstance(m, AIMessage)]
        self.step = len(replies)
        self._calls = sum(len(reply.tool_calls) for reply in replies)
        self._results = [m for m in later if isinstance(m, ToolMessage)]
        self._history = [m for m in messages if isinstance(m, ToolMessage)]

    def calls(self, *items: tuple[str, dict[str, JsonValue]]) -> ChatResponse:
        """Return a response that calls the tools ``items`` as ``(name, arguments)``."""
        return ChatResponse(
            tool_calls=[
                ToolCall(
                    id=f"call_{self.turn_id}_{self._calls + number}",
                    name=name,
                    arguments=arguments,
                )
                for number, (name, arguments) in enumerate(items, start=1)
            ]
        )

    def follow_ups(
        self, *preferred: str
    ) -> tuple[tuple[str, dict[str, JsonValue]], ...]:
        """Return a ``suggest_follow_ups`` call with questions not asked yet.

        The questions come from ``preferred``, then from ``QUESTIONS``, up to
        three. With fewer than two left, there is no call.
        """
        candidates = dict.fromkeys([*preferred, *QUESTIONS])
        fresh = [q for q in candidates if question_key(q) not in self.asked][:3]
        return (follow_ups(*fresh),) if len(fresh) >= 2 else ()

    def last_question_note(self) -> str:
        """Return a sentence naming the one prepared question not asked yet.

        It is ``""`` unless exactly one is left: with two or more, the
        follow-up suggestions offer them.
        """
        left = [q for q in QUESTIONS if question_key(q) not in self.asked]
        if len(left) != 1:
            return ""
        return f' One prepared question is left: "{left[0]}"'

    def result[M: BaseModel](self, name: str, kind: type[M]) -> M | None:
        """Return the latest result of tool ``name`` in the turn as ``kind``."""
        return latest(self._results, name, kind)

    def saved(self) -> Memory | None:
        """Return the memory saved last in the conversation, or ``None``."""
        return latest(self._history, "save_memory", Memory)

    def memory_note(self) -> str:
        """Return a sentence quoting the best memory the turn found, or ``""``."""
        found = self.result("search_memory", MemorySearchResult)
        if found is None or not found.results:
            return ""
        return f"From memory: {inline(sentence(found.results[0].content))} "


def latest[M: BaseModel](
    messages: list[ToolMessage], name: str, kind: type[M]
) -> M | None:
    """Return the last result of tool ``name`` in ``messages`` as ``kind``.

    Parameters
    ----------
    messages
        Tool messages, oldest first.
    name
        The tool name.
    kind
        The model of the result.

    Returns
    -------
    M | None
        ``None`` when the tool has no result there or its last one is an
        error.
    """
    for message in reversed(messages):
        if message.name == name:
            try:
                return kind.model_validate_json(message.content or "")
            except ValidationError:
                return None
    return None


def chart_call(
    kind: Literal["bar", "line"], title: str, x: str, y: str
) -> tuple[str, dict[str, JsonValue]]:
    """Return a ``make_chart`` call for ``DemoTurn.calls``.

    Parameters
    ----------
    kind
        "bar" or "line".
    title
        The chart title.
    x, y
        The result columns for the axes.

    Returns
    -------
    tuple[str, dict[str, JsonValue]]
        The tool name and its arguments.
    """
    return "make_chart", {"kind": kind, "title": title, "x": x, "y": y}


def follow_ups(*questions: str) -> tuple[str, dict[str, JsonValue]]:
    """Return a ``suggest_follow_ups`` call for ``DemoTurn.calls``.

    Parameters
    ----------
    *questions
        Two or three follow-up questions.

    Returns
    -------
    tuple[str, dict[str, JsonValue]]
        The tool name and its arguments.
    """
    return "suggest_follow_ups", {"questions": list(questions)}


def sql_call(sql: str) -> tuple[str, dict[str, JsonValue]]:
    """Return a ``run_sql`` call for ``DemoTurn.calls``.

    Parameters
    ----------
    sql
        The query.

    Returns
    -------
    tuple[str, dict[str, JsonValue]]
        The tool name and its arguments.
    """
    return "run_sql", {"sql": sql}


LIST_TABLES: tuple[str, dict[str, JsonValue]] = ("list_tables", {})
SEARCH_MEMORY: tuple[str, dict[str, JsonValue]] = (
    "search_memory",
    {"query": MEMORY_QUERY},
)


class DemoChat:
    """A scripted model for the demo: plays the model's side of five questions.

    It answers the questions in ``QUESTIONS``, picked by keywords in the
    turn's user message, and lists the tables with their row counts for any
    other message. Each request gets the next step of that answer: real tool
    calls, which the agent runs on demo_shop, then text written from the
    tool results in the request. ``CATEGORY_REVENUE`` asks how to count
    revenue through ``ask_user`` and reads the answer. Both revenue
    questions and the report search the memory for "revenue" and quote the
    best match. ``REPORT`` loads the ``report`` skill, gives
    ``REPORT_TOPICS`` to sub-agents, whose requests this chat answers too,
    writes the report from their answers and streams slowly so it can be
    stopped. A message that starts with "remember" saves the rest to
    memory; one that starts with "forget" or "do not remember" searches the
    memory and deletes the best match that holds every word of the request
    but the most common ones. When it says only "that" or "it", it lists the
    memory and deletes the memory saved last in the thread. Responses stream
    through ``ScriptedChat``. No model is called and no usage is reported.

    Parameters
    ----------
    pace
        Seconds between streamed words; a response with tool calls waits
        five times as long.
    slow_pace
        Seconds between the words of the report.
    analyst_pace
        Seconds between the words of a sub-agent's answer.
    """

    def __init__(
        self, *, pace: float = 0.03, slow_pace: float = 0.08, analyst_pace: float = 0.06
    ) -> None:
        self.model = "scripted"
        self._pace = pace
        self._slow_pace = slow_pace
        self._analyst_pace = analyst_pace

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Return the next step of the answer."""
        response, _ = self.respond(request)
        return response

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamChunk]:
        """Stream the next step of the answer, word by word at the demo's pace."""
        response, pace = self.respond(request)
        if response.tool_calls:
            await asyncio.sleep(self._pace * 5)
        words = 0
        async for chunk in ScriptedChat([response]).stream(request):
            if chunk.content_delta:
                if words:
                    await asyncio.sleep(pace)
                words += 1
            yield chunk

    def respond(self, request: ChatRequest) -> tuple[ChatResponse, float]:
        """Return the next response of the answer and the pace of its words."""
        turn = DemoTurn(request)
        if turn.analyst:
            return self._analyst(turn), self._analyst_pace
        if forget := FORGET.match(turn.message):
            return self._forget(turn, forget["fact"].strip()), self._pace
        if remember := REMEMBER.match(turn.message):
            return self._remember(turn, remember["fact"].strip()), self._pace
        message = turn.message.lower()
        routes: list[tuple[tuple[str, ...], Callable[[DemoTurn], ChatResponse]]] = [
            (("report",), self._report),
            (("categor",), self._category_revenue),
            (("return",), self._returns_per_store),
            (("month",), self._revenue_per_month),
            (("unit", "product"), self._top_products),
        ]
        answer, pace = self._overview, self._pace
        for keywords, route in routes:
            if any(keyword in message for keyword in keywords):
                answer = route
                pace = self._slow_pace if route == self._report else self._pace
                break
        response = answer(turn)
        if response.tool_calls or response.content is None:
            return response, pace
        content = response.content + turn.last_question_note()
        return response.model_copy(update={"content": content}), pace

    def _top_products(self, turn: DemoTurn) -> ChatResponse:
        if turn.step == 0:
            return turn.calls(LIST_TABLES)
        if turn.step == 1:
            return turn.calls(sql_call(TOP_PRODUCTS_SQL))
        if turn.step == 2:
            return turn.calls(
                chart_call("bar", "Units sold per product", "product", "units"),
                *turn.follow_ups(REVENUE_PER_MONTH, CATEGORY_REVENUE, REPORT),
            )
        result = self._rows(turn)
        first, second, third = result.rows[:3]
        return ChatResponse(
            content=unapplied_note(turn.constraints, "all completed orders")
            + f"{first[0]} sold the most units: {first[1]} in completed orders. "
            f"{second[0]} follows with {second[1]}, then {third[0]} with {third[1]}. "
            f"The chart shows the top {result.row_count}."
        )

    def _revenue_per_month(self, turn: DemoTurn) -> ChatResponse:
        if turn.step == 0:
            return turn.calls(LIST_TABLES, SEARCH_MEMORY)
        if turn.step == 1:
            return turn.calls(sql_call(REVENUE_PER_MONTH_SQL))
        if turn.step == 2:
            return turn.calls(
                chart_call("line", "Revenue per month", "month", "revenue"),
                *turn.follow_ups(TOP_PRODUCTS, RETURNS_PER_STORE, REPORT),
            )
        result = self._rows(turn)
        best = max(result.rows, key=lambda row: float(str(row[1])))
        low = min(result.rows, key=lambda row: float(str(row[1])))
        years: dict[str, float] = {}
        for month, revenue in result.rows:
            year = str(month)[:4]
            years[year] = years.get(year, 0.0) + float(str(revenue))
        totals = " and ".join(
            f"{euros(total)} in {year}" for year, total in years.items()
        )
        return ChatResponse(
            content=turn.memory_note()
            + unapplied_note(turn.constraints, "all completed orders")
            + f"Revenue peaks in {best[0]} at {euros(best[1])}. The lowest "
            f"month is {low[0]} with {euros(low[1])}. Completed orders brought in "
            f"{totals}."
        )

    def _returns_per_store(self, turn: DemoTurn) -> ChatResponse:
        if turn.step == 0:
            return turn.calls(LIST_TABLES)
        if turn.step == 1:
            return turn.calls(sql_call(RETURNS_FIRST_TRY_SQL))
        if turn.step == 2:
            return turn.calls(sql_call(RETURNS_SQL))
        if turn.step == 3:
            return turn.calls(
                chart_call("bar", "Returns per store", "store", "returns"),
                *turn.follow_ups(CATEGORY_REVENUE, REPORT),
            )
        result = self._rows(turn)
        first, second = result.rows[:2]
        last = result.rows[-1]
        return ChatResponse(
            content=unapplied_note(turn.constraints, "all returns")
            + f"{first[0]} has the most returns ({first[1]}), followed by "
            f"{second[0]} ({second[1]}). {last[0]} has the fewest ({last[1]}). "
            "Returns of online orders are counted under Online shop. The first "
            "query failed because returns has no order_id column, so the second "
            "one joins through order_items."
        )

    def _category_revenue(self, turn: DemoTurn) -> ChatResponse:
        if turn.step == 0:
            return turn.calls(LIST_TABLES, SEARCH_MEMORY)
        if turn.step == 1:
            return turn.calls(
                (
                    "ask_user",
                    {
                        "question": "How should revenue be counted?",
                        "proposals": [p.model_dump() for p in PROPOSALS],
                        "allow_free_text": True,
                    },
                )
            )
        proposal, preface = self._reading(turn)
        column = "share" if proposal.id == "share" else "revenue"
        if turn.step == 2:
            return turn.calls(sql_call(CATEGORY_SQL[proposal.id]))
        if turn.step == 3:
            return turn.calls(
                chart_call("bar", f"{proposal.label} per category", "category", column),
                *turn.follow_ups(TOP_PRODUCTS, REVENUE_PER_MONTH),
            )
        first, second = self._rows(turn).rows[:2]
        if column == "share":
            detail = (
                f"{first[0]} makes up {first[1]}% of all revenue, "
                f"{second[0]} {second[1]}%."
            )
        else:
            detail = (
                f"{first[0]} leads with {euros(first[1])}, followed by {second[0]} "
                f"with {euros(second[1])}."
            )
        return ChatResponse(content=f"{turn.memory_note()}{preface}{detail}")

    def _report(self, turn: DemoTurn) -> ChatResponse:
        if turn.step == 0:
            return turn.calls(("load_skill", {"name": REPORT_SKILL}), SEARCH_MEMORY)
        if turn.step == 1:
            topics: list[JsonValue] = [topic.model_dump() for topic in REPORT_TOPICS]
            return turn.calls(("analyze_topics", {"topics": topics}))
        suggestions = turn.follow_ups(RETURNS_PER_STORE, CATEGORY_REVENUE)
        if turn.step == 2 and suggestions:
            return turn.calls(*suggestions)
        findings = turn.result("analyze_topics", TopicFindings)
        answers = {
            found.name: found.answer
            for found in (findings.findings if findings is not None else [])
            if found.answer
        }
        constraints = [c for c in turn.constraints if c != "2025"]
        parts = [
            turn.memory_note()
            + unapplied_note(constraints, "completed orders in 2025")
            + "Sales by store in 2025, counting completed orders only.",
            *(
                f"{label}: {answers[name]}"
                if name in answers
                else f"The {name} part has no answer."
                for name, label in REPORT_PARTS
            ),
        ]
        return ChatResponse(content="\n\n".join(parts))

    def _analyst(self, turn: DemoTurn) -> ChatResponse:
        sql = REPORT_TOPIC_SQL.get(turn.message)
        if sql is None:
            return ChatResponse(
                content="This scripted sub-agent only answers the parts of the "
                "demo report."
            )
        if turn.step == 0:
            return turn.calls(sql_call(sql))
        rows = self._rows(turn).rows
        if not rows:
            return ChatResponse(content="The query found no rows.")
        if sql == REPORT_SQL:
            return ChatResponse(content=self._revenue_part(rows))
        if sql == REPORT_RETURNS_SQL:
            return ChatResponse(content=self._returns_part(rows))
        return ChatResponse(content=self._products_part(rows))

    @staticmethod
    def _revenue_part(rows: list[list[SqlValue]]) -> str:
        lines = [
            f"{store} took {count} orders worth {euros(revenue)}."
            for store, count, revenue in rows
        ]
        total = sum(float(str(row[2])) for row in rows)
        orders = sum(int(str(row[1])) for row in rows)
        share = 100 * float(str(rows[0][2])) / total
        return (
            " ".join(lines)
            + f" Together that is {euros(total)} from {orders:,} orders, and "
            f"{rows[0][0]} alone brings in {share:.1f}% of it."
        )

    @staticmethod
    def _returns_part(rows: list[list[SqlValue]]) -> str:
        first, last = rows[0], rows[-1]
        return (
            f"{first[0]} had the most returned items, {first[1]} with "
            f"{euros(first[2])} refunded, and {last[0]} the fewest, {last[1]}."
        )

    @staticmethod
    def _products_part(rows: list[list[SqlValue]]) -> str:
        first, second, third = rows[:3]
        return (
            f"{first[0]} sold the most units ({first[1]}), followed by "
            f"{second[0]} ({second[1]}) and {third[0]} ({third[1]})."
        )

    def _remember(self, turn: DemoTurn, fact: str) -> ChatResponse:
        if turn.step == 0:
            if not re.search(r"\w", fact):
                return ChatResponse(content=REMEMBER_HINT)
            if len(sentence(fact)) > CONTENT_LENGTH:
                return ChatResponse(content=TOO_LONG)
            return turn.calls(("save_memory", memory_fields(fact)))
        saved = turn.result("save_memory", Memory)
        if saved is None:
            return ChatResponse(content="I could not save that to memory.")
        return ChatResponse(content=f"I will remember that {inline(saved.content)}")

    def _forget(self, turn: DemoTurn, fact: str) -> ChatResponse:
        fact = fact.rstrip(" .!")
        if fact.lower() in LAST_MEMORY:
            return self._forget_last(turn)
        words = search_words(fact)
        if turn.step == 0:
            return turn.calls(("search_memory", {"query": words}))
        found = turn.result("search_memory", MemorySearchResult)
        matches = [] if found is None else found.results
        target = next((m for m in matches if holds_words(m, words)), None)
        if target is None:
            return ChatResponse(content=f'Nothing in memory matches "{fact}".')
        if turn.step == 1:
            return turn.calls(("delete_memory", {"key": target.key}))
        return self._removed(turn, target)

    def _forget_last(self, turn: DemoTurn) -> ChatResponse:
        saved = turn.saved()
        if saved is None:
            return ChatResponse(content=FORGET_HINT)
        if turn.step == 0:
            return turn.calls(("list_memories", {}))
        listing = turn.result("list_memories", MemoryListResult)
        entries = [] if listing is None else listing.entries
        target = next((m for m in entries if m.key == saved.key), None)
        if target is None:
            return ChatResponse(content="That memory was already gone.")
        if turn.step == 1:
            return turn.calls(("delete_memory", {"key": target.key}))
        return self._removed(turn, target)

    @staticmethod
    def _removed(turn: DemoTurn, target: Memory) -> ChatResponse:
        deleted = turn.result("delete_memory", MemoryDeleteResult)
        if deleted is None:
            return ChatResponse(content="I could not remove that from memory.")
        if not deleted.deleted:
            return ChatResponse(content="That memory was already gone.")
        return ChatResponse(
            content=f"Removed from memory: {inline(sentence(target.content))}"
        )

    def _overview(self, turn: DemoTurn) -> ChatResponse:
        if turn.step == 0:
            return turn.calls(LIST_TABLES)
        if turn.step == 1:
            listing = turn.result("list_tables", TableList)
            names = [] if listing is None else [t.name for t in listing.tables]
            sql = " UNION ALL ".join(
                f"SELECT '{name}' AS table_name, count(*) AS row_count FROM {name}"
                for name in names
            )
            return turn.calls(sql_call(sql))
        suggestions = turn.follow_ups(TOP_PRODUCTS, CATEGORY_REVENUE, REPORT)
        if turn.step == 2 and suggestions:
            return turn.calls(*suggestions)
        return ChatResponse(
            content="This is a scripted demo without a model, so it only answers a "
            f"few prepared questions. Here are the tables of {DEMO_DATABASE} with "
            "their row counts."
            + (" Try one of the questions below." if suggestions else "")
        )

    @staticmethod
    def _rows(turn: DemoTurn) -> SqlResult:
        result = turn.result("run_sql", SqlResult)
        if result is None:
            return SqlResult(
                sql="", columns=[], rows=[], row_count=0, truncated=False, elapsed_ms=0
            )
        return result

    @staticmethod
    def _reading(turn: DemoTurn) -> tuple[Proposal, str]:
        answer = turn.result("ask_user", ClarificationAnswer)
        if answer is not None and answer.proposal is not None:
            note = unapplied_note(turn.constraints, "all completed orders")
            return answer.proposal, f"{note}{answer.proposal.label}: "
        text = "" if answer is None else answer.answer.strip()
        basis = reading_basis(text)
        proposal = next(p for p in PROPOSALS if p.id == (basis or "gross"))
        reading = READINGS[proposal.id]
        if basis is None:
            preface = (
                f'"{text}" does not say how to count revenue, so I used {reading}. '
            )
        else:
            preface = f'I read "{text}" as {reading}. '
        named = [*turn.constraints, *unapplied_constraints(text)]
        constraints = list({c.casefold(): c for c in named}.values())
        return proposal, preface + unapplied_note(constraints, "all completed orders")


def copy_demo_database(source: Database, databases_dir: Path) -> None:
    """Copy the demo database and its sidecar into ``databases_dir``."""
    databases_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source.path, databases_dir / source.path.name)
    sidecar = source.path.with_suffix(".json")
    if sidecar.is_file():
        shutil.copyfile(sidecar, databases_dir / sidecar.name)
