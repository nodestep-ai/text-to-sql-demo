from pathlib import Path

from pydantic import DirectoryPath, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings


class ServerSettings(BaseSettings):
    """What the web app reads from environment variables: data, limits, web build.

    Nothing reads a ``.env`` file. Pass one explicitly with
    ``uv run --no-sync --env-file .env ...``.
    """

    data_dir: Path = Field(
        default=Path("data"),
        validation_alias="TEXT_TO_SQL_DEMO_DATA_DIR",
        description=(
            "Folder with databases/ (the SQLite files), threads.json, the "
            "agent's state.jsonl and agent.json, and by default its memory and skills."
        ),
    )
    memory_dir: Path | None = Field(
        default=None,
        validation_alias="TEXT_TO_SQL_DEMO_MEMORY_DIR",
        description="Folder whose memories/ holds the agent's memory; unset, TEXT_TO_SQL_DEMO_DATA_DIR.",
    )
    skills_dir: DirectoryPath | None = Field(
        default=None,
        validation_alias="TEXT_TO_SQL_DEMO_SKILLS_DIR",
        description="Existing folder with your own SKILL.md files; unset, skills/ in TEXT_TO_SQL_DEMO_DATA_DIR.",
    )
    max_rows: int = Field(
        default=200,
        gt=0,
        validation_alias="TEXT_TO_SQL_DEMO_MAX_ROWS",
        description="Most rows a query returns; more rows set the truncated flag.",
    )
    query_timeout_ms: int = Field(
        default=5000,
        gt=0,
        validation_alias="TEXT_TO_SQL_DEMO_QUERY_TIMEOUT_MS",
        description="Time limit for one query in milliseconds.",
    )
    web_dist: Path = Field(
        default=Path("web/dist"),
        validation_alias="TEXT_TO_SQL_DEMO_WEB_DIST",
        description="Built web app served at /. Without it only the API runs.",
    )
    otlp_endpoint: str | None = Field(
        default=None,
        min_length=1,
        validation_alias="TEXT_TO_SQL_DEMO_OTLP_ENDPOINT",
        description="nodeartifact (OTLP/HTTP) address the agent's traces go to; unset or empty, nothing is traced.",
    )

    @field_validator("otlp_endpoint", "memory_dir", "skills_dir", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def databases_dir(self) -> Path:
        """The folder with the ``*.sqlite`` files: ``data_dir / "databases"``."""
        return self.data_dir / "databases"

    @property
    def memory_folder(self) -> Path:
        """The folder of the memory: ``memory_dir``, else ``data_dir``."""
        return self.memory_dir or self.data_dir

    @property
    def skills_folder(self) -> Path:
        """The folder of your own skills: ``skills_dir``, else ``data_dir / "skills"``."""
        return self.skills_dir or self.data_dir / "skills"


class Settings(ServerSettings):
    """``ServerSettings`` plus the model settings the agent needs."""

    model: str = Field(
        default="gpt-6-luna",
        min_length=1,
        validation_alias="TEXT_TO_SQL_DEMO_MODEL",
        description="OpenAI model name used by the agent.",
    )
    reasoning_effort: str = Field(
        default="none",
        min_length=1,
        validation_alias="TEXT_TO_SQL_DEMO_REASONING_EFFORT",
        description=(
            "Reasoning effort sent with every model request. The agent uses "
            "the Chat Completions API, where gpt-6-luna takes tools only with none."
        ),
    )
    openai_api_key: SecretStr = Field(
        ...,
        min_length=1,
        validation_alias="OPENAI_API_KEY",
        description="OpenAI API key.",
    )
