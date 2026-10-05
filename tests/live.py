import asyncio
import json
from types import TracebackType
from typing import Any, Self

from starlette.types import ASGIApp, Message

TIMEOUT = 5


class LiveStream:
    """One request to an ASGI app, read while the response streams.

    ``TestClient`` and httpx's ``ASGITransport`` return a response only after
    the app finished it. This driver runs the app in a task on the test's
    event loop, hands out server-sent events as they arrive and can drop the
    connection in the middle.
    """

    def __init__(self, app: ASGIApp, path: str, body: object) -> None:
        self._app = app
        self._scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"test"),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
        }
        self._body = json.dumps(body).encode()
        self._body_sent = False
        self._gone = asyncio.Event()
        self._messages: asyncio.Queue[Message] = asyncio.Queue()
        self._buffer = ""
        self._task: asyncio.Task[None] | None = None
        self.status = 0

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(self._call())
        start = await self._message()
        self.status = start["status"]
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.disconnect()

    async def event(self) -> tuple[str, dict[str, Any]]:
        """Return the next event as ``(type, data)``."""
        while "\n\n" not in self._buffer:
            message = await self._message()
            self._buffer += message.get("body", b"").decode()
            if not message.get("more_body", False) and "\n\n" not in self._buffer:
                raise EOFError("the response ended")
        block, self._buffer = self._buffer.split("\n\n", 1)
        event_line, data_line = block.split("\n")
        return event_line.removeprefix("event: "), json.loads(
            data_line.removeprefix("data: ")
        )

    async def rest(self) -> list[tuple[str, dict[str, Any]]]:
        """Return the events up to and including ``done``."""
        events = [await self.event()]
        while events[-1][0] != "done":
            events.append(await self.event())
        return events

    async def disconnect(self) -> None:
        """Drop the connection and wait until the app has returned."""
        self._gone.set()
        if self._task is not None:
            await asyncio.wait_for(self._task, TIMEOUT)

    async def _call(self) -> None:
        await self._app(self._scope, self._receive, self._send)

    async def _receive(self) -> Message:
        if not self._body_sent:
            self._body_sent = True
            return {"type": "http.request", "body": self._body, "more_body": False}
        await self._gone.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: Message) -> None:
        self._messages.put_nowait(message)

    async def _message(self) -> Message:
        return await asyncio.wait_for(self._messages.get(), TIMEOUT)
