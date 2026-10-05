import re
import sqlite3
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from text_to_sql_demo.database import Database, table_names
from text_to_sql_demo.errors import DatabaseNotFoundError

DATABASE_ID = re.compile(r"[a-z0-9_-]+")
SUFFIX = ".sqlite"
SQLITE_HEADER = b"SQLite format 3\x00"


class DatabaseInfo(BaseModel):
    """A database in the database list."""

    id: str
    title: str
    description: str
    table_count: int
    size_bytes: int
    examples: list[str]


class Sidecar(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    description: str = ""
    examples: list[str] = []

    @field_validator("title", "description", mode="before")
    @classmethod
    def _text_or_empty(cls, value: object) -> object:
        return value if isinstance(value, str) else ""

    @field_validator("examples", mode="before")
    @classmethod
    def _texts_only(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]


class DatabaseRegistry:
    """The SQLite files in one folder, found again on every call.

    A file counts when its name is ``<id>.sqlite`` with an id of lowercase
    letters, digits, ``_`` and ``-``, it resolves to a regular file inside
    the folder, it starts with the SQLite header and SQLite can read its
    list of tables. A file that is still being copied or is damaged is left
    out until it can be read. A symlink that points outside the folder is
    refused. An optional ``<id>.json`` next to it holds
    ``{"title": str, "description": str, "examples": [str]}``; the examples
    are questions the web app offers for the database. A field of the wrong
    type counts as missing and an example that is not text is left out, so
    one bad value does not drop the rest of the sidecar.

    Parameters
    ----------
    folder
        The folder with the ``.sqlite`` files. A missing folder holds none.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def databases(self) -> list[DatabaseInfo]:
        """Return every database, sorted by id."""
        found = [self._info(database) for database in self._scan().values()]
        return [info for info in found if info is not None]

    def get(self, database_id: str) -> Database:
        """Return the database with this id.

        Raises
        ------
        DatabaseNotFoundError
            If the folder has no database with this id, or SQLite cannot
            read the file.
        """
        database = self._scan().get(database_id)
        if database is None:
            raise DatabaseNotFoundError(f"Database {database_id!r} not found.")
        if self._table_count(database) is None:
            raise DatabaseNotFoundError(
                f"Database {database_id!r} cannot be opened. "
                "The file may be incomplete or damaged."
            )
        return database

    def _scan(self) -> dict[str, Database]:
        if not self.folder.is_dir():
            return {}
        root = self.folder.resolve()
        found: dict[str, Database] = {}
        for entry in self.folder.iterdir():
            database_id = entry.name.removesuffix(SUFFIX)
            if entry.suffix != SUFFIX or not DATABASE_ID.fullmatch(database_id):
                continue
            path = self._inside(root, entry)
            if path is not None and self._is_sqlite(path):
                found[database_id] = Database(database_id, path)
        return dict(sorted(found.items()))

    def _info(self, database: Database) -> DatabaseInfo | None:
        table_count = self._table_count(database)
        if table_count is None:
            return None
        sidecar = self._sidecar(database)
        return DatabaseInfo(
            id=database.id,
            title=sidecar.title.strip() or database.id,
            description=sidecar.description.strip(),
            table_count=table_count,
            size_bytes=database.path.stat().st_size,
            examples=[text for text in map(str.strip, sidecar.examples) if text],
        )

    def _sidecar(self, database: Database) -> Sidecar:
        path = self._inside(self.folder.resolve(), self.folder / f"{database.id}.json")
        if path is None:
            return Sidecar()
        try:
            return Sidecar.model_validate_json(path.read_bytes())
        except (OSError, ValidationError):
            return Sidecar()

    @staticmethod
    def _table_count(database: Database) -> int | None:
        try:
            with database.connect() as connection:
                return len(table_names(connection))
        except sqlite3.Error:
            return None

    @staticmethod
    def _inside(root: Path, entry: Path) -> Path | None:
        try:
            path = entry.resolve()
        except (OSError, RuntimeError):
            return None
        if path.is_relative_to(root) and path.is_file():
            return path
        return None

    @staticmethod
    def _is_sqlite(path: Path) -> bool:
        try:
            with path.open("rb") as file:
                return file.read(len(SQLITE_HEADER)) == SQLITE_HEADER
        except OSError:
            return False
