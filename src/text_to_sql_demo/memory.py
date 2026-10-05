from datetime import datetime
from pathlib import Path
from typing import Self
from urllib.parse import quote

from nodestep import Tool, tool
from nodestep.middleware import FilesystemMemory, Memory, MemoryListResult
from nodestep.middleware.memory import delete_memory_from_disk, save_memory_to_disk
from pydantic import BaseModel, ConfigDict, Field

from text_to_sql_demo.errors import MemoryNotFoundError
from text_to_sql_demo.threads import new_id

MEMORY_SCOPE = "global"
KEY_LENGTH = 60
KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
TITLE_LENGTH = 120
CONTENT_LENGTH = 2000


class MemoryText(BaseModel):
    """Body of ``POST /api/memories`` and ``PUT /api/memories/{key}``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=TITLE_LENGTH)
    content: str = Field(min_length=1, max_length=CONTENT_LENGTH)


class MemoryNote(MemoryText):
    """The arguments of the agent's ``save_memory``: ``MemoryText`` and a key."""

    key: str = Field(max_length=KEY_LENGTH, pattern=KEY_PATTERN)


class MemoryEntry(BaseModel):
    """One memory as the API shows it."""

    key: str
    title: str
    content: str
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def of(cls, memory: Memory) -> Self:
        """Return the API view of a stored memory."""
        return cls(
            key=memory.key,
            title=memory.title,
            content=memory.content,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
        )


class MemoryList(BaseModel):
    """Every memory, the latest change first.

    ``skipped`` names the memory files that are left out: files that are not
    memory JSON, or whose name or scope does not match their memory.
    """

    memories: list[MemoryEntry]
    skipped: list[str]


class MemoryStore:
    """The agent's memory as the web app reads and changes it.

    One memory for the whole app: the files of nodestep's
    ``FilesystemMemory(folder, scope=MEMORY_SCOPE)``, in
    ``folder/memories/global/<key>.json``. The agent and the web app read and
    write the same files. The web app lists and changes only the files whose
    name and scope match the memory they hold.

    Parameters
    ----------
    folder
        The memory folder, ``ServerSettings.memory_folder``. It is created on
        the first write.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def tools(self) -> list[Tool]:
        """Return the agent's memory tools over the same files.

        ``save_memory`` takes what ``MemoryNote`` allows, so the Memory tab
        can edit every memory the agent saves. ``search_memory``,
        ``list_memories`` and ``delete_memory`` are nodestep's.
        """
        nodestep_tools = FilesystemMemory(self.folder, scope=MEMORY_SCOPE).tools()
        return [
            self._save_tool(),
            *(item for item in nodestep_tools if item.name != "save_memory"),
        ]

    def entries(self) -> MemoryList:
        """Return every memory, the latest change first.

        A file that is not memory JSON, or whose name or scope does not match
        its memory, is left out and named in ``skipped``.
        """
        loaded = self._load()
        return MemoryList(
            memories=[MemoryEntry.of(memory) for memory in loaded.entries],
            skipped=loaded.skipped,
        )

    def add(self, text: MemoryText) -> MemoryEntry:
        """Store a new memory under a new key and return it."""
        memory = Memory(
            key=new_id(), title=text.title, content=text.content, scope=MEMORY_SCOPE
        )
        return MemoryEntry.of(save_memory_to_disk(self.folder, memory))

    def edit(self, key: str, text: MemoryText) -> MemoryEntry:
        """Change the title and content of the memory ``key`` and return it.

        Raises
        ------
        MemoryNotFoundError
            If ``entries`` lists no memory with this key.
        """
        memory = self._find(key)
        changed = memory.model_copy(
            update={"title": text.title, "content": text.content}
        )
        return MemoryEntry.of(save_memory_to_disk(self.folder, changed))

    def delete(self, key: str) -> None:
        """Delete the memory ``key``.

        Raises
        ------
        MemoryNotFoundError
            If ``entries`` lists no memory with this key.
        """
        self._find(key)
        if not delete_memory_from_disk(self.folder, MEMORY_SCOPE, key):
            raise MemoryNotFoundError(f"Memory {key!r} not found.")

    def _find(self, key: str) -> Memory:
        for memory in self._load().entries:
            if memory.key == key:
                return memory
        raise MemoryNotFoundError(f"Memory {key!r} not found.")

    def _load(self) -> MemoryListResult:
        loaded = MemoryListResult(scope=MEMORY_SCOPE, entries=[])
        for path in sorted((self.folder / "memories" / MEMORY_SCOPE).glob("*.json")):
            try:
                memory = Memory.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded.skipped.append(path.name)
                continue
            if memory.scope != MEMORY_SCOPE or path.name != file_name(memory.key):
                loaded.skipped.append(path.name)
                continue
            loaded.entries.append(memory)
        loaded.entries.sort(
            key=lambda memory: (
                memory.updated_at.timestamp() if memory.updated_at else 0.0,
                memory.key,
            ),
            reverse=True,
        )
        return loaded

    def _save_tool(self) -> Tool:
        folder = self.folder

        @tool(input_model=MemoryNote)
        def save_memory(title: str, content: str, key: str) -> Memory:
            """Save a memory that later answers in every conversation can use.

            Parameters
            ----------
            title
                A short title, at most 120 characters.
            content
                The fact as one or two sentences, at most 2,000 characters.
            key
                A short name such as revenue-rule: letters, digits, "-" and
                "_", at most 60 characters. A memory with the same key is
                replaced.
            """
            memory = Memory(key=key, title=title, content=content, scope=MEMORY_SCOPE)
            return save_memory_to_disk(folder, memory)

        return save_memory


def file_name(key: str) -> str:
    """Return the name of the file that holds the memory ``key``, as nodestep names it."""
    return f"{quote(key, safe='')}.json"
