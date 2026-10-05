from pathlib import Path

import pytest
from pydantic import ValidationError

from text_to_sql_demo.settings import ServerSettings, Settings

ALIASES = [
    "TEXT_TO_SQL_DEMO_DATA_DIR",
    "TEXT_TO_SQL_DEMO_MODEL",
    "TEXT_TO_SQL_DEMO_REASONING_EFFORT",
    "OPENAI_API_KEY",
    "TEXT_TO_SQL_DEMO_MAX_ROWS",
    "TEXT_TO_SQL_DEMO_QUERY_TIMEOUT_MS",
    "TEXT_TO_SQL_DEMO_WEB_DIST",
    "TEXT_TO_SQL_DEMO_OTLP_ENDPOINT",
    "TEXT_TO_SQL_DEMO_MEMORY_DIR",
    "TEXT_TO_SQL_DEMO_SKILLS_DIR",
]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ALIASES:
        monkeypatch.delenv(name, raising=False)


def test_reads_environment_with_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    assert settings.model == "gpt-6-luna"
    assert settings.reasoning_effort == "none"
    assert settings.openai_api_key.get_secret_value() == "sk-test"
    assert settings.data_dir == Path("data")
    assert settings.databases_dir == Path("data/databases")
    assert settings.max_rows == 200
    assert settings.query_timeout_ms == 5000
    assert settings.web_dist == Path("web/dist")
    assert settings.otlp_endpoint is None
    assert settings.memory_folder == Path("data")
    assert settings.skills_folder == Path("data/skills")


def test_reads_every_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name, value in {
        "TEXT_TO_SQL_DEMO_DATA_DIR": "/srv/text-to-sql-demo",
        "TEXT_TO_SQL_DEMO_MODEL": "other-model",
        "TEXT_TO_SQL_DEMO_REASONING_EFFORT": "low",
        "OPENAI_API_KEY": "sk-test",
        "TEXT_TO_SQL_DEMO_MAX_ROWS": "50",
        "TEXT_TO_SQL_DEMO_QUERY_TIMEOUT_MS": "250",
        "TEXT_TO_SQL_DEMO_WEB_DIST": "/srv/web",
        "TEXT_TO_SQL_DEMO_OTLP_ENDPOINT": "http://nodeartifact:4318",
        "TEXT_TO_SQL_DEMO_MEMORY_DIR": "/srv/memory",
        "TEXT_TO_SQL_DEMO_SKILLS_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings()
    assert settings.model == "other-model"
    assert settings.reasoning_effort == "low"
    assert settings.data_dir == Path("/srv/text-to-sql-demo")
    assert settings.databases_dir == Path("/srv/text-to-sql-demo/databases")
    assert settings.max_rows == 50
    assert settings.query_timeout_ms == 250
    assert settings.web_dist == Path("/srv/web")
    assert settings.otlp_endpoint == "http://nodeartifact:4318"
    assert settings.memory_folder == Path("/srv/memory")
    assert settings.skills_folder == tmp_path


def test_the_memory_and_skills_folders_follow_the_data_folder(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_DATA_DIR", "/srv/text-to-sql-demo")
    settings = ServerSettings()
    assert settings.memory_folder == Path("/srv/text-to-sql-demo")
    assert settings.skills_folder == Path("/srv/text-to-sql-demo/skills")
    moved = settings.model_copy(update={"data_dir": Path("/tmp/demo")})
    assert moved.memory_folder == Path("/tmp/demo")
    assert moved.skills_folder == Path("/tmp/demo/skills")


def test_an_empty_tracing_endpoint_means_no_tracing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_OTLP_ENDPOINT", "")
    assert ServerSettings().otlp_endpoint is None


@pytest.mark.parametrize(
    "name", ["TEXT_TO_SQL_DEMO_MEMORY_DIR", "TEXT_TO_SQL_DEMO_SKILLS_DIR"]
)
def test_an_empty_folder_setting_means_the_default(
    monkeypatch: pytest.MonkeyPatch, name: str
):
    monkeypatch.setenv(name, "")
    settings = ServerSettings()
    assert settings.memory_folder == Path("data")
    assert settings.skills_folder == Path("data/skills")


@pytest.mark.parametrize("name", ["missing", "file.md"])
def test_a_skills_folder_that_is_not_a_folder_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
):
    (tmp_path / "file.md").write_text("", encoding="utf-8")
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_SKILLS_DIR", str(tmp_path / name))
    with pytest.raises(ValidationError) as info:
        ServerSettings()
    assert info.value.errors()[0]["loc"] == ("TEXT_TO_SQL_DEMO_SKILLS_DIR",)


def test_there_is_no_single_database_setting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_MODEL", "m")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_DATABASE", "/srv/sales.sqlite")
    settings = Settings()
    assert "database" not in Settings.model_fields
    assert "/srv/sales.sqlite" not in repr(settings)


def test_only_the_key_is_required():
    with pytest.raises(ValidationError) as error:
        Settings()
    missing = {entry["loc"][0] for entry in error.value.errors()}
    assert missing == {"OPENAI_API_KEY"}


def test_the_model_must_not_be_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_MODEL", "")
    with pytest.raises(ValidationError, match="TEXT_TO_SQL_DEMO_MODEL"):
        Settings()


def test_dotenv_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".env").write_text(
        "TEXT_TO_SQL_DEMO_MODEL=x\nOPENAI_API_KEY=y\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "name", ["TEXT_TO_SQL_DEMO_MAX_ROWS", "TEXT_TO_SQL_DEMO_QUERY_TIMEOUT_MS"]
)
def test_limits_must_be_positive(monkeypatch: pytest.MonkeyPatch, name: str):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_MODEL", "m")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValidationError, match=name):
        Settings()


def test_key_is_masked(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_MODEL", "m")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    assert "sk-secret" not in repr(Settings())


def test_env_example_lists_every_setting():
    lines = (
        (Path(__file__).parents[1] / ".env.example")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    names = {
        line.split("=", 1)[0] for line in lines if line and not line.startswith("#")
    }
    assert names == {field.validation_alias for field in Settings.model_fields.values()}


def test_the_server_settings_need_no_model_or_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEXT_TO_SQL_DEMO_DATA_DIR", "/srv/text-to-sql-demo")
    settings = ServerSettings()
    assert settings.databases_dir == Path("/srv/text-to-sql-demo/databases")
    assert "model" not in ServerSettings.model_fields
    assert "openai_api_key" not in ServerSettings.model_fields


def test_settings_add_the_model_to_the_server_settings():
    assert set(Settings.model_fields) == set(ServerSettings.model_fields) | {
        "model",
        "reasoning_effort",
        "openai_api_key",
    }
