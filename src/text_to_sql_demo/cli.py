import signal
import tempfile
from pathlib import Path
from types import FrameType
from typing import Annotated

import typer
import uvicorn
from fastapi import FastAPI
from nodestep import OpenAIChat
from nodestep.chat import Chat
from nodestep.middleware import (
    MemoryConfigError,
    SkillConflictError,
    SkillFormatError,
)
from pydantic import ValidationError

from text_to_sql_demo.app import create_app
from text_to_sql_demo.demo import DEMO_DATABASE, QUESTIONS, DemoChat, copy_demo_database
from text_to_sql_demo.errors import DatabaseNotFoundError
from text_to_sql_demo.nodestep_agent import NodestepAgent
from text_to_sql_demo.registry import DatabaseRegistry
from text_to_sql_demo.settings import ServerSettings, Settings
from text_to_sql_demo.tracing import OPENAI_PROVIDER, SCRIPTED_PROVIDER

HOST = "127.0.0.1"
PORT = 8000
SCRIPTED_MODEL = "scripted"
MISSING_KEY = (
    "text-to-sql-demo serve needs OPENAI_API_KEY. Put it in a .env file (see .env.example) "
    "and pass the file: from a checkout, run "
    "'uv run --no-sync --env-file .env text-to-sql-demo serve'; for the container image, "
    "run 'docker run --env-file .env ...' or list the file under env_file in "
    "compose. Or try 'text-to-sql-demo demo', which needs no key."
)

app = typer.Typer(
    name="text-to-sql-demo",
    help=(
        "Ask questions about SQLite databases in plain language. 'demo' runs "
        "without a model or an API key."
    ),
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Address to bind.")] = HOST,
    port: Annotated[int, typer.Option(help="Port to bind.")] = PORT,
) -> None:
    """Run the API and the web app with the agent on an OpenAI model.

    Settings come from environment variables; see .env.example. Needs
    OPENAI_API_KEY; pass a .env file with
    'uv run --no-sync --env-file .env text-to-sql-demo serve'.
    """
    settings = load_settings(Settings)
    agent = load_agent(
        OpenAIChat.from_env(
            model=settings.model, reasoning_effort=settings.reasoning_effort
        ),
        settings,
        model=settings.model,
        provider_name=OPENAI_PROVIDER,
    )
    server = create_app(settings, agent)
    if not DatabaseRegistry(settings.databases_dir).databases():
        typer.echo(
            f"text-to-sql-demo: no databases in {settings.databases_dir}. Copy a .sqlite file "
            "there, or run 'uv run --no-sync python scripts/make_demo_db.py'.",
            err=True,
        )
    warn_without_web_app(settings)
    try:
        run_server(server, host=host, port=port)
    finally:
        agent.close()


@app.command()
def demo(
    host: Annotated[str, typer.Option(help="Address to bind.")] = HOST,
    port: Annotated[int, typer.Option(help="Port to bind.")] = PORT,
) -> None:
    """Run the app over demo_shop with a scripted model.

    No model is called and no API key is needed; the agent still runs the
    SQL and draws the charts from demo_shop. Threads and memory go to a
    temporary folder that is deleted when the demo stops; your skills are
    read as for serve.
    """
    settings = load_settings(ServerSettings)
    try:
        source = DatabaseRegistry(settings.databases_dir).get(DEMO_DATABASE)
    except DatabaseNotFoundError:
        typer.echo(
            f"text-to-sql-demo demo: no {DEMO_DATABASE}.sqlite in {settings.databases_dir}. "
            "Run 'uv run --no-sync python scripts/make_demo_db.py' first.",
            err=True,
        )
        raise typer.Exit(code=2) from None
    warn_without_web_app(settings)
    with tempfile.TemporaryDirectory(prefix="text-to-sql-demo-") as folder:
        demo_settings = settings.model_copy(
            update={
                "data_dir": Path(folder),
                "memory_dir": None,
                "skills_dir": settings.skills_folder,
            }
        )
        copy_demo_database(source, demo_settings.databases_dir)
        agent = load_agent(
            DemoChat(),
            demo_settings,
            model=SCRIPTED_MODEL,
            provider_name=SCRIPTED_PROVIDER,
        )
        questions = "\n".join(f"  {question}" for question in QUESTIONS)
        typer.echo(
            f"text-to-sql-demo demo: scripted model over {DEMO_DATABASE}, no API key needed. "
            f"It answers:\n{questions}",
            err=True,
        )
        try:
            run_server(create_app(demo_settings, agent), host=host, port=port)
        finally:
            agent.close()


def load_agent(
    chat: Chat, settings: ServerSettings, *, model: str, provider_name: str
) -> NodestepAgent:
    """Build the agent, or exit with code 2 naming a skill or memory folder it cannot use."""
    try:
        return NodestepAgent.from_settings(
            chat, settings, model=model, provider_name=provider_name
        )
    except (SkillFormatError, SkillConflictError, MemoryConfigError) as error:
        typer.echo(f"text-to-sql-demo: {error}", err=True)
        raise typer.Exit(code=2) from None


def run_server(server: FastAPI, *, host: str, port: int) -> None:
    """Run ``server`` with uvicorn until it stops.

    After a graceful shutdown on SIGTERM, uvicorn raises the signal again.
    While the server runs, that signal raises ``SystemExit`` with code 143,
    so cleanup still runs, as it does after Ctrl-C.

    Parameters
    ----------
    server
        The app from ``create_app``.
    host, port
        Where to bind.
    """
    previous = signal.signal(signal.SIGTERM, exit_on_terminate)
    try:
        uvicorn.run(server, host=host, port=port)
    finally:
        signal.signal(signal.SIGTERM, previous)


def exit_on_terminate(signal_number: int, frame: FrameType | None) -> None:
    """Raise ``SystemExit`` with the exit code of a process ended by the signal."""
    raise SystemExit(128 + signal_number)


def load_settings[S: ServerSettings](kind: type[S]) -> S:
    """Read ``kind`` from the environment, or exit with code 2 naming the problems."""
    try:
        return kind()
    except ValidationError as error:
        problems = error.errors(include_input=False)
        lines = [
            f"  {'.'.join(str(part) for part in entry['loc'])}: {entry['msg']}"
            for entry in problems
        ]
        if any(entry["loc"] == ("OPENAI_API_KEY",) for entry in problems):
            lines.append(MISSING_KEY)
        typer.echo("text-to-sql-demo: invalid settings\n" + "\n".join(lines), err=True)
        raise typer.Exit(code=2) from None


def warn_without_web_app(settings: ServerSettings) -> None:
    """Say on stderr when there is no built web app to serve."""
    if not settings.web_dist.is_dir():
        typer.echo(
            f"text-to-sql-demo: no web app at {settings.web_dist}; serving the API only. "
            "Run 'npm run build' in web/ or set TEXT_TO_SQL_DEMO_WEB_DIST.",
            err=True,
        )


def main() -> None:
    """Run the ``text-to-sql-demo`` command."""
    app(prog_name="text-to-sql-demo")
