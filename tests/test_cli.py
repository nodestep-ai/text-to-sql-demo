import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Collection
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nodestep import OpenAIChat
from nodestep.chat import Chat
from nodestep.middleware import FilesystemSkills
from typer.testing import CliRunner, Result

from make_demo_db import DemoShopBuilder
from text_to_sql_demo.cli import app, main
from text_to_sql_demo.demo import DemoChat
from text_to_sql_demo.nodestep_agent import NodestepAgent
from text_to_sql_demo.settings import ServerSettings

VARIABLES = [
    "TEXT_TO_SQL_DEMO_DATA_DIR",
    "TEXT_TO_SQL_DEMO_MODEL",
    "OPENAI_API_KEY",
    "TEXT_TO_SQL_DEMO_MAX_ROWS",
    "TEXT_TO_SQL_DEMO_QUERY_TIMEOUT_MS",
    "TEXT_TO_SQL_DEMO_WEB_DIST",
    "TEXT_TO_SQL_DEMO_OTLP_ENDPOINT",
    "TEXT_TO_SQL_DEMO_MEMORY_DIR",
    "TEXT_TO_SQL_DEMO_SKILLS_DIR",
    "OPENAI_BASE_URL",
]

type Served = list[tuple[FastAPI, str, int]]


def invoke(*args: str) -> Result:
    return CliRunner().invoke(app, list(args))


ANSI_STYLE = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return " ".join(ANSI_STYLE.sub("", text).replace("│", " ").split())


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def configured(
    monkeypatch: pytest.MonkeyPatch, database_path: Path, data_dir: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_WEB_DIST", str(tmp_path / "no-dist"))


@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> Served:
    calls: Served = []

    def record(app: FastAPI, *, host: str, port: int) -> None:
        calls.append((app, host, port))

    monkeypatch.setattr(uvicorn, "run", record)
    return calls


def test_serve_help_names_the_options():
    result = invoke("serve", "--help")
    assert result.exit_code == 0
    text = plain(result.output)
    assert "Run the API and the web app with the agent on an OpenAI model." in text
    assert "--host" in text
    assert "--port" in text
    assert "127.0.0.1" in text
    assert "8000" in text


def test_main_runs_the_command_line(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", ["text-to-sql-demo", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0


@pytest.mark.usefixtures("configured")
def test_serve_binds_localhost_by_default(no_server: Served):
    assert invoke("serve").exit_code == 0
    [(_, host, port)] = no_server
    assert (host, port) == ("127.0.0.1", 8000)


def test_missing_settings_are_named():
    result = invoke("serve")
    assert result.exit_code == 2
    assert "OPENAI_API_KEY" in result.stderr
    assert "TEXT_TO_SQL_DEMO_MODEL" not in result.stderr


def test_a_missing_key_says_how_to_pass_it():
    text = plain(invoke("serve").stderr)
    assert "text-to-sql-demo serve needs OPENAI_API_KEY" in text
    assert "uv run --no-sync --env-file .env text-to-sql-demo serve" in text
    assert "text-to-sql-demo demo" in text


def test_a_missing_key_says_how_to_pass_it_to_a_container():
    text = plain(invoke("serve").stderr)
    assert "docker run --env-file .env" in text
    assert "env_file" in text


def test_an_empty_key_says_how_to_pass_it(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    result = invoke("serve")
    assert result.exit_code == 2
    text = plain(result.stderr)
    assert "OPENAI_API_KEY" in text
    assert "text-to-sql-demo serve needs OPENAI_API_KEY" in text
    assert "text-to-sql-demo demo" in text


def test_the_env_example_names_the_same_command():
    example = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "uv run --no-sync --env-file .env text-to-sql-demo serve" in example


type Built = list[tuple[Chat, str, str]]


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> Built:
    agents: Built = []
    original = NodestepAgent.from_settings.__func__

    def record(
        cls: type[NodestepAgent],
        chat: Chat,
        settings: ServerSettings,
        *,
        model: str,
        provider_name: str,
    ) -> NodestepAgent:
        agents.append((chat, model, provider_name))
        return original(cls, chat, settings, model=model, provider_name=provider_name)

    monkeypatch.setattr(NodestepAgent, "from_settings", classmethod(record))
    return agents


@pytest.mark.usefixtures("configured", "no_server")
def test_serve_runs_the_nodestep_agent_with_openai(built: Built):
    assert invoke("serve").exit_code == 0
    [(chat, model, provider_name)] = built
    assert isinstance(chat, OpenAIChat)
    assert chat.model == "gpt-6-luna"
    assert model == "gpt-6-luna"
    assert provider_name == "openai"


@pytest.fixture
def closed(monkeypatch: pytest.MonkeyPatch) -> list[NodestepAgent]:
    agents: list[NodestepAgent] = []
    monkeypatch.setattr(NodestepAgent, "close", lambda self: agents.append(self))
    return agents


@pytest.mark.usefixtures("configured", "no_server")
def test_serve_closes_the_agent_when_the_server_stops(closed: list[NodestepAgent]):
    assert invoke("serve").exit_code == 0
    assert len(closed) == 1


@pytest.mark.usefixtures("configured")
def test_serve_closes_the_agent_when_the_server_fails(
    closed: list[NodestepAgent], monkeypatch: pytest.MonkeyPatch
):
    def fail(app: FastAPI, *, host: str, port: int) -> None:
        raise OSError("address in use")

    monkeypatch.setattr(uvicorn, "run", fail)
    assert invoke("serve").exit_code == 1
    assert len(closed) == 1


@pytest.mark.usefixtures("configured", "no_server")
def test_serve_uses_the_model_setting(built: Built, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_MODEL", "gpt-6-luna-mini")
    assert invoke("serve").exit_code == 0
    [(chat, _, _)] = built
    assert isinstance(chat, OpenAIChat)
    assert chat.model == "gpt-6-luna-mini"


@pytest.mark.usefixtures("configured", "no_server")
def test_serve_turns_reasoning_off_by_default(built: Built):
    assert invoke("serve").exit_code == 0
    [(chat, _, _)] = built
    assert isinstance(chat, OpenAIChat)
    assert chat.request_parameters == {"reasoning_effort": "none"}


@pytest.mark.usefixtures("configured", "no_server")
def test_serve_sends_the_reasoning_effort_setting(
    built: Built, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_REASONING_EFFORT", "low")
    assert invoke("serve").exit_code == 0
    [(chat, _, _)] = built
    assert isinstance(chat, OpenAIChat)
    assert chat.request_parameters == {"reasoning_effort": "low"}


def test_secret_is_not_printed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_MAX_ROWS", "0")
    result = invoke("serve")
    assert result.exit_code == 2
    assert "TEXT_TO_SQL_DEMO_MAX_ROWS" in result.stderr
    assert "sk-secret-value" not in result.output


@pytest.mark.usefixtures("configured", "no_server")
def test_serve_without_databases_says_how_to_add_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    (tmp_path / "dist").mkdir()
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_WEB_DIST", str(tmp_path / "dist"))
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_DATA_DIR", str(tmp_path / "empty"))
    result = invoke("serve")
    assert result.exit_code == 0
    assert str(tmp_path / "empty" / "databases") in result.stderr
    assert "make_demo_db.py" in result.stderr


@pytest.mark.usefixtures("configured")
def test_serve_starts_with_a_broken_database_file(
    databases_dir: Path, no_server: Served
):
    (databases_dir / "broken.sqlite").write_bytes(b"SQLite format 3\x00")
    assert invoke("serve").exit_code == 0
    assert len(no_server) == 1


@pytest.mark.usefixtures("configured")
def test_serve_runs_uvicorn(no_server: Served):
    assert invoke("serve", "--host", "0.0.0.0", "--port", "9000").exit_code == 0
    [(served, host, port)] = no_server
    assert isinstance(served, FastAPI)
    assert (host, port) == ("0.0.0.0", 9000)


@pytest.mark.usefixtures("configured", "no_server")
def test_serve_says_when_the_web_app_is_missing(tmp_path: Path):
    result = invoke("serve")
    assert result.exit_code == 0
    assert str(tmp_path / "no-dist") in result.stderr
    assert "npm run build" in result.stderr
    assert "TEXT_TO_SQL_DEMO_WEB_DIST" in result.stderr


@pytest.mark.usefixtures("configured", "no_server")
def test_serve_is_quiet_when_the_web_app_is_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    (tmp_path / "dist").mkdir()
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_WEB_DIST", str(tmp_path / "dist"))
    result = invoke("serve")
    assert result.exit_code == 0
    assert result.stderr == ""


@pytest.fixture
def demo_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    data = tmp_path / "data"
    DemoShopBuilder().build(data / "databases" / "demo_shop.sqlite")
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_DATA_DIR", str(data))
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_WEB_DIST", str(tmp_path / "no-dist"))
    return data


@pytest.fixture
def temporary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


def test_demo_help_says_it_is_scripted():
    result = invoke("demo", "--help")
    assert result.exit_code == 0
    text = plain(result.output)
    assert "scripted" in text
    assert "No model is called" in text
    assert "no API key" in text
    assert "demo_shop" in text
    assert "--port" in text
    assert "--host" in text


def test_the_command_list_names_both_commands():
    text = plain(invoke("--help").output)
    assert "serve" in text
    assert "demo" in text
    assert "without a model" in text


def test_demo_serves_only_demo_shop_without_a_key(
    monkeypatch: pytest.MonkeyPatch, demo_data: Path, temporary: Path
):
    (demo_data / "databases" / "other.sqlite").write_bytes(
        (demo_data / "databases" / "demo_shop.sqlite").read_bytes()
    )
    served: list[tuple[str, int]] = []
    listed: list[list[str]] = []
    endings: list[str] = []
    kinds: list[str] = []
    folders: list[str] = []

    def record(app: FastAPI, *, host: str, port: int) -> None:
        client = TestClient(app)
        served.append((host, port))
        listed.append(
            [database["id"] for database in client.get("/api/databases").json()]
        )
        response = client.post(
            "/api/chat",
            json={"thread_id": None, "database": "demo_shop", "message": "hello"},
        )
        lines = [line for line in response.text.splitlines() if line]
        endings.append(lines[-2])
        kinds.extend(line for line in lines if line.startswith("event: "))
        folders.extend(path.name for path in temporary.iterdir())

    monkeypatch.setattr(uvicorn, "run", record)
    result = invoke("demo", "--port", "9001")
    assert result.exit_code == 0, result.output
    assert served == [("127.0.0.1", 9001)]
    assert listed == [["demo_shop"]]
    assert endings == ["event: done"]
    assert "event: sql_result" in kinds
    assert "event: suggestions" in kinds
    assert "event: usage" not in kinds
    [folder] = folders
    assert folder.startswith("text-to-sql-demo-")
    assert list(temporary.iterdir()) == []
    assert not (demo_data / "threads.json").exists()
    assert "scripted" in result.stderr


def test_demo_keeps_its_memory_in_its_own_folder(
    monkeypatch: pytest.MonkeyPatch, demo_data: Path, temporary: Path, tmp_path: Path
):
    configured = tmp_path / "memory"
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_MEMORY_DIR", str(configured))
    saved: list[str] = []

    def record(app: FastAPI, *, host: str, port: int) -> None:
        client = TestClient(app)
        memory = {"title": "Revenue", "content": "Completed orders only."}
        assert client.post("/api/memories", json=memory).status_code == 201
        saved.extend(
            path.relative_to(temporary).as_posix() for path in temporary.rglob("*.json")
        )

    monkeypatch.setattr(uvicorn, "run", record)
    assert invoke("demo").exit_code == 0
    assert any("/memories/global/" in path for path in saved)
    assert not configured.exists()
    assert not (demo_data / "memories").exists()


def write_broken_skill(folder: Path) -> Path:
    path = folder / "broken" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("no frontmatter\n", encoding="utf-8")
    return path


@pytest.mark.usefixtures("configured", "no_server")
def test_serve_with_a_broken_skill_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_SKILLS_DIR", str(tmp_path / "skills"))
    path = write_broken_skill(tmp_path / "skills")
    result = invoke("serve")
    assert result.exit_code == 2
    assert result.stderr.startswith("text-to-sql-demo: ")
    assert str(path) in result.stderr


@pytest.mark.usefixtures("demo_data", "no_server")
def test_demo_with_a_broken_skill_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_SKILLS_DIR", str(tmp_path / "skills"))
    path = write_broken_skill(tmp_path / "skills")
    result = invoke("demo")
    assert result.exit_code == 2
    assert str(path) in result.stderr


@pytest.mark.usefixtures("no_server")
def test_demo_reads_the_skills_in_the_data_folder(demo_data: Path):
    path = write_broken_skill(demo_data / "skills")
    result = invoke("demo")
    assert result.exit_code == 2
    assert str(path) in result.stderr


@pytest.mark.usefixtures("demo_data", "no_server")
def test_demo_offers_the_skills_in_the_data_folder(
    demo_data: Path, monkeypatch: pytest.MonkeyPatch
):
    folder = demo_data / "skills" / "refunds"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: refunds\ndescription: How refunds are counted\n---\n",
        encoding="utf-8",
    )
    seen: list[Path] = []
    original = FilesystemSkills.__init__

    def record(
        self: FilesystemSkills, roots: list[Path], *, nodes: Collection[str]
    ) -> None:
        seen.extend(roots)
        original(self, roots, nodes=nodes)

    monkeypatch.setattr(FilesystemSkills, "__init__", record)
    assert invoke("demo").exit_code == 0
    assert demo_data / "skills" in seen


@pytest.mark.parametrize("command", ["serve", "demo"])
@pytest.mark.usefixtures("configured", "no_server")
def test_a_skills_folder_that_does_not_exist_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_SKILLS_DIR", str(tmp_path / "skils"))
    result = invoke(command)
    assert result.exit_code == 2
    assert "TEXT_TO_SQL_DEMO_SKILLS_DIR" in result.stderr


@pytest.mark.usefixtures("configured", "no_server")
def test_a_memory_folder_that_is_a_file_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    path = tmp_path / "memory.txt"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_MEMORY_DIR", str(path))
    result = invoke("serve")
    assert result.exit_code == 2
    assert result.stderr.startswith("text-to-sql-demo: ")
    assert str(path) in result.stderr


@pytest.mark.usefixtures("configured", "no_server")
def test_a_skill_that_is_not_utf8_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    path = tmp_path / "skills" / "latin" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"---\nname: latin\ndescription: caf\xe9\n---\n")
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_SKILLS_DIR", str(tmp_path / "skills"))
    result = invoke("serve")
    assert result.exit_code == 2
    assert str(path) in result.stderr


@pytest.mark.usefixtures("no_server")
def test_demo_without_demo_shop_says_how_to_make_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_DATA_DIR", str(tmp_path / "empty"))
    result = invoke("demo")
    assert result.exit_code == 2
    assert "demo_shop" in result.stderr
    assert "make_demo_db.py" in result.stderr


@pytest.mark.usefixtures("demo_data", "no_server")
def test_demo_runs_the_nodestep_agent_with_the_scripted_model(built: Built):
    assert invoke("demo").exit_code == 0
    [(chat, model, provider_name)] = built
    assert isinstance(chat, DemoChat)
    assert model == "scripted"
    assert provider_name == "scripted"


def test_demo_binds_port_8000_by_default(demo_data: Path, no_server: Served):
    assert invoke("demo").exit_code == 0
    [(_, host, port)] = no_server
    assert (host, port) == ("127.0.0.1", 8000)


def test_demo_binds_the_address_given(demo_data: Path, no_server: Served):
    assert invoke("demo", "--host", "0.0.0.0", "--port", "9000").exit_code == 0
    [(_, host, port)] = no_server
    assert (host, port) == ("0.0.0.0", 9000)


@pytest.mark.usefixtures("demo_data", "no_server")
def test_demo_closes_the_agent_when_the_server_stops(closed: list[NodestepAgent]):
    assert invoke("demo").exit_code == 0
    assert len(closed) == 1


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_until_up(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, "the demo exited before it served"
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/databases"):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("the demo did not start")


@pytest.mark.skipif(
    sys.platform == "win32", reason="SIGTERM ends a process at once on Windows"
)
def test_demo_removes_its_folder_when_terminated(demo_data: Path, tmp_path: Path):
    folder = tmp_path / "terminated"
    folder.mkdir()
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from text_to_sql_demo.cli import main; main()",
            "demo",
            "--port",
            str(port),
        ],
        env={**os.environ, "TMPDIR": str(folder)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_until_up(port, process)
        [created] = list(folder.iterdir())
        assert created.name.startswith("text-to-sql-demo-")
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=30) == 128 + signal.SIGTERM
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert list(folder.iterdir()) == []
