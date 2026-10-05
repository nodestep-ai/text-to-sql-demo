# Changelog

## [0.1.0a1] - 2026-10-05

First alpha.

### Added

- `text-to-sql-demo serve`: the API and the web app, with the agent on an OpenAI model.
- `text-to-sql-demo demo`: the same app with a scripted model and a generated demo database. No API key needed.
- The agent: it looks up tables, runs SQL, draws charts, asks when a question is unclear and suggests follow-ups.
- A memory the agent saves to only when you ask, editable in the Memory tab. Skills from `SKILL.md` files, and sub-agents for the parts of a report.
- Read-only query checks, and limits on rows, time and result size.
- Stored threads: stop an answer, edit a question and switch between its versions.
- Any number of SQLite databases in `data/databases/`, one per thread.
- Tracing to nodeartifact with `TEXT_TO_SQL_DEMO_OTLP_ENDPOINT`, and Docker files that run both.

[0.1.0a1]: https://github.com/nodestep-ai/text-to-sql-demo/releases/tag/v0.1.0a1
