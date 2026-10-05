# API

The web app uses a JSON API under `/api`. An answer comes back as a stream of server-sent events. The server has no OpenAPI page.

## Ask a question

A new thread has `thread_id` set to null and names its database:

```sh
curl -N http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"thread_id": null, "database": "demo_shop", "message": "Which ten products sold the most units?"}'
```

With `text-to-sql-demo demo`, the stream starts and ends like this (shortened):

```text
event: run
data: {"thread_id":"efabdbcd41ff4d06aed753ccd4a9b27a","run_id":"bf5d3d8e0dfd419d92e165d9cc5d6715","database":"demo_shop"}

event: tool
data: {"call_id":"call_6cee39d1459d40fd997060e2d7b910fa_2","name":"run_sql","status":"started","arguments":{"sql":"SELECT p.name AS product, ..."},"summary":null}

event: sql_result
data: {"sql":"SELECT\n  p.name AS product, ...","columns":["product","units"],"rows":[["Dark chocolate 70%",247], ...], ...}

event: token
data: {"text":"Dark"}

event: final
data: {"text":"Dark chocolate 70% sold the most units: 247 in completed orders. ..."}

event: done
data: {}
```

Each event has an `event:` line with the type and a `data:` line with JSON. A stream starts with `run` and ends with `done`. [Events](#events) lists every type.

## Continue a thread

Send the next question with the `thread_id` from the `run` event. A thread stays on its database, so `database` may be null:

```sh
curl -N http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "efabdbcd41ff4d06aed753ccd4a9b27a", "database": null, "message": "What is the revenue per month?"}'
```

`GET /api/threads/{thread_id}` returns the thread with its turns, for replay.

## Answer a clarification

When a question is unclear, the stream sends a `clarification` event and ends, and the thread waits for the answer. In the demo, "Which category brings in the most revenue?" asks:

```text
event: clarification
data: {"key":"act:clarify","question":"How should revenue be counted?","proposals":[{"id":"gross","label":"Gross revenue","description":"..."}, ...],"allow_free_text":true}
```

Answer on that thread with the `id` of a proposal, or with free text when `allow_free_text` is true. The rest of the answer streams like a new question:

```sh
curl -N http://127.0.0.1:8000/api/threads/e10f2345208f4e9b97fd0b5b5a9bf541/resume \
  -H "Content-Type: application/json" \
  -d '{"key": "act:clarify", "answer": "gross"}'
```

While the thread waits, a new question on it gets 409 `clarification_pending`. Answer the question or stop it first.

## Stop, edit and switch versions

- `POST /api/threads/{thread_id}/stop` cancels the running answer. The stream sends `stopped` and then `done`, and the answer so far is kept with status `stopped`. A client that drops the stream stops the answer the same way.
- When the thread waits for a clarification, stop closes the question instead: its turn gets status `stopped`, and the thread counts as busy until the stop request returns. Otherwise stop does nothing.
- `POST /api/threads/{thread_id}/turns/{turn_id}/edit` replaces a user turn and everything after it with a new version, and streams the new answer. The earlier versions are kept, and a user turn keeps its id in all of them. An assistant turn or an answer to a clarification cannot be edited (422).
- `POST /api/threads/{thread_id}/turns/{turn_id}/versions/{index}` shows another version of a turn; the turns after it follow that version.

## Memory

There is one memory for the whole app; see [Memory](../README.md#memory). A memory is `{"key", "title", "content", "created_at", "updated_at"}`.

- `POST /api/memories` makes up a new key. The agent picks its own keys.
- `title` takes 1 to 120 characters and `content` 1 to 2,000, both trimmed. A body that breaks this gets 422 and changes nothing.
- `PUT` and `DELETE` get 404 `memory_not_found` for a key that the list does not show.
- `skipped` in `GET /api/memories` names the memory files that are left out: files that are not memory JSON, or whose name or scope does not match their memory. The routes do not change them.

## Routes

| Route | Returns |
|---|---|
| `GET /api/databases` | `[{"id", "title", "description", "table_count", "size_bytes", "examples"}]`, sorted by id |
| `GET /api/databases/{id}/schema` | `{"database", "tables": [...]}` |
| `POST /api/chat` with `{"thread_id": str \| null, "database": str \| null, "message": str}` | event stream |
| `POST /api/threads/{thread_id}/resume` with `{"key": str, "answer": str}` | event stream |
| `POST /api/threads/{thread_id}/turns/{turn_id}/edit` with `{"message": str}` | event stream |
| `POST /api/threads/{thread_id}/turns/{turn_id}/versions/{index}` | the thread, as in `GET /api/threads/{thread_id}` |
| `POST /api/threads/{thread_id}/stop` | 204, no body |
| `GET /api/threads` | `[{"id", "title", "database", "updated_at"}]`, the latest first; the title is the first question of the version shown |
| `GET /api/threads/{thread_id}` | `{"id", "title", "database", "turns"}` |
| `GET /api/memories` | `{"memories": [...], "skipped": [str]}`, the latest change first |
| `POST /api/memories` with `{"title": str, "content": str}` | 201, the new memory |
| `PUT /api/memories/{key}` with `{"title": str, "content": str}` | the changed memory |
| `DELETE /api/memories/{key}` | 204, no body |

## Turns

Each turn in `GET /api/threads/{thread_id}` is `{"id", "role", "text", "frames", "status", "version", "answers"}`:

- `role` is `user` or `assistant`.
- `status` is `completed`, `stopped`, `error` or `waiting`. An assistant turn is `waiting` while its clarification has no answer, and `error` when the agent failed or sent an `error` event. User turns are `completed`.
- `frames` holds the events of the answer between `run` and `done`, each as its data plus a `type` key, except `final` and `stopped`. Text the model wrote before a tool call or a clarification is kept as one `token` frame in its place, so a replay shows it where it streamed. The answer text is the turn's `text`.
- `version` is `{"index", "count"}` on a user turn that has been edited, and null otherwise. `index` counts from 0; the web app shows 1 to `count`.
- `answers` is the `key` of the clarification that a user turn answers, and null for every other turn.

## Events

A `call_id` is unique within a thread, also across a clarification and its answer and across the versions of an edited question.

| Event | Data |
|---|---|
| `run` | `thread_id`, `run_id`, `database` |
| `token` | `text` |
| `tool` | `call_id`, `name`, `status` (`started`, `finished`, `error`), `arguments`, `summary`. Also for the memory tools, `load_skill` and `analyze_topics`; a `delete_memory` that finds no memory is an `error` |
| `sql_result` | `call_id`, `sql` (the checked query as it ran, formatted over several lines), `columns`, `rows`, `row_count`, `truncated`, `elapsed_ms`. The queries of the sub-agents come between the `started` and `finished` events of their `analyze_topics` call, with the `call_id` `<call id>/<topic>/<query>`, counted from 1 |
| `chart` | `call_id`, `kind` (`bar`, `line`), `title`, `x`, `y`, `data` |
| `clarification` | `key`, `question`, `proposals` (2 to 4 of `{"id", "label", "description"}`), `allow_free_text` |
| `final` | `text` |
| `suggestions` | `questions`: 2 or 3 follow-up questions not asked in the thread yet, sent after `final`; left out when fewer than 2 are left |
| `usage` | `model`, `input_tokens`, `output_tokens` of one model call; the demo sends none |
| `error` | `kind`, `message` |
| `stopped` | nothing |
| `done` | nothing |

## Errors

An error answer is `{"detail", "code"}`:

| Status | `code` |
|---|---|
| 404 | `thread_not_found`, `database_not_found`, `turn_not_found`, `version_not_found`, `memory_not_found` |
| 409 | `run_active`, `clarification_pending`, `clarification_not_pending`, `database_mismatch` |
| 422 | `database_required`, `invalid_answer`, `turn_not_editable` |
| 500 | `database_unreadable` |

Every check runs before anything is streamed or stored.

| Request | Fails with |
|---|---|
| A new thread without `database` | 422 `database_required` |
| An unknown database, or a thread's database that was removed or cannot be opened | 404 `database_not_found` |
| An existing thread with another `database` | 409 `database_mismatch` |
| Chat, edit, resume or the versions route while an answer runs on the thread | 409 `run_active` |
| Chat while the thread waits for a clarification | 409 `clarification_pending` |
| Resume when the thread does not wait for the question with that key | 409 `clarification_not_pending` |
| Resume with an `answer` that is neither a proposal `id` nor free text the question allows | 422 `invalid_answer` |
| An invalid request body | 422 with FastAPI's list of problems in `detail` and no `code` |
