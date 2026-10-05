import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from text_to_sql_demo.errors import DatabaseNotFoundError
from text_to_sql_demo.registry import SQLITE_HEADER, DatabaseInfo, DatabaseRegistry


def make(folder: Path, name: str, *, tables: int = 1) -> Path:
    path = folder / name
    with closing(sqlite3.connect(path)) as setup:
        for number in range(tables):
            setup.execute(f"CREATE TABLE t{number} (a INTEGER)")
        setup.commit()
    return path


def ids(registry: DatabaseRegistry) -> list[str]:
    return [info.id for info in registry.databases()]


def header_only(path: Path) -> None:
    path.write_bytes(SQLITE_HEADER)


def header_and_garbage(path: Path) -> None:
    path.write_bytes(SQLITE_HEADER + b"\xff" * 4080)


def cut_short(path: Path) -> None:
    with closing(sqlite3.connect(path)) as setup:
        setup.execute("CREATE TABLE big (a TEXT)")
        setup.executemany("INSERT INTO big VALUES (?)", [("x" * 200,)] * 200)
        setup.commit()
    path.write_bytes(path.read_bytes()[:4096])


def wal_mode(path: Path) -> None:
    with closing(sqlite3.connect(path)) as setup:
        setup.execute("PRAGMA journal_mode = WAL")
        setup.execute("CREATE TABLE t0 (a INTEGER)")
        setup.commit()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "databases"
    path.mkdir()
    return path


@pytest.fixture
def registry(folder: Path) -> DatabaseRegistry:
    return DatabaseRegistry(folder)


def test_lists_databases_sorted_by_id(folder: Path, registry: DatabaseRegistry):
    sales = make(folder, "sales.sqlite", tables=2)
    shop = make(folder, "demo_shop.sqlite", tables=3)
    (folder / "sales.json").write_text(
        json.dumps({"title": "Sales", "description": "Orders and invoices."}),
        encoding="utf-8",
    )
    assert registry.databases() == [
        DatabaseInfo(
            id="demo_shop",
            title="demo_shop",
            description="",
            table_count=3,
            size_bytes=shop.stat().st_size,
            examples=[],
        ),
        DatabaseInfo(
            id="sales",
            title="Sales",
            description="Orders and invoices.",
            table_count=2,
            size_bytes=sales.stat().st_size,
            examples=[],
        ),
    ]


def test_info_dump_has_the_contract_keys(folder: Path, registry: DatabaseRegistry):
    make(folder, "sales.sqlite")
    [info] = registry.databases()
    assert set(info.model_dump()) == {
        "id",
        "title",
        "description",
        "table_count",
        "size_bytes",
        "examples",
    }


def test_ids_may_use_digits_dashes_and_underscores(
    folder: Path, registry: DatabaseRegistry
):
    for name in ["a-1.sqlite", "b_2.sqlite", "3.sqlite"]:
        make(folder, name)
    assert ids(registry) == ["3", "a-1", "b_2"]


def test_the_list_is_sorted_by_id_not_by_file_name(
    folder: Path, registry: DatabaseRegistry
):
    for name in ["demo_3.sqlite", "demo-2.sqlite", "demo.sqlite"]:
        make(folder, name)
    assert ids(registry) == ["demo", "demo-2", "demo_3"]


def test_sidecar_without_title_uses_the_id(folder: Path, registry: DatabaseRegistry):
    make(folder, "sales.sqlite")
    (folder / "sales.json").write_text('{"description": "Orders."}', encoding="utf-8")
    [info] = registry.databases()
    assert (info.title, info.description) == ("sales", "Orders.")


def test_sidecar_examples_are_listed_in_order(folder: Path, registry: DatabaseRegistry):
    make(folder, "sales.sqlite")
    (folder / "sales.json").write_text(
        json.dumps({"examples": [" Which month sold most? ", "", "Top customers?"]}),
        encoding="utf-8",
    )
    [info] = registry.databases()
    assert info.examples == ["Which month sold most?", "Top customers?"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"title": 3}',
        "[]",
        '{"examples": "Top customers?"}',
    ],
)
def test_broken_sidecar_is_ignored(
    folder: Path, registry: DatabaseRegistry, content: str
):
    make(folder, "sales.sqlite")
    (folder / "sales.json").write_text(content, encoding="utf-8")
    [info] = registry.databases()
    assert (info.title, info.description, info.examples) == ("sales", "", [])


@pytest.mark.parametrize("examples", [None, 3, {"question": "Top?"}, "Top?"])
def test_bad_examples_keep_the_title_and_description(
    folder: Path, registry: DatabaseRegistry, examples: object
):
    make(folder, "sales.sqlite")
    (folder / "sales.json").write_text(
        json.dumps({"title": "Sales", "description": "Orders.", "examples": examples}),
        encoding="utf-8",
    )
    [info] = registry.databases()
    assert (info.title, info.description, info.examples) == ("Sales", "Orders.", [])


def test_examples_that_are_not_text_are_left_out(
    folder: Path, registry: DatabaseRegistry
):
    make(folder, "sales.sqlite")
    (folder / "sales.json").write_text(
        json.dumps({"examples": ["Top customers?", 3, None, {"q": 1}, ["x"]]}),
        encoding="utf-8",
    )
    [info] = registry.databases()
    assert info.examples == ["Top customers?"]


@pytest.mark.parametrize("field", ["title", "description"])
@pytest.mark.parametrize("value", [None, 3, ["Sales"]])
def test_a_bad_title_or_description_keeps_the_other_fields(
    folder: Path, registry: DatabaseRegistry, field: str, value: object
):
    make(folder, "sales.sqlite")
    sidecar = {"title": "Sales", "description": "Orders.", "examples": ["Top?"]}
    (folder / "sales.json").write_text(
        json.dumps(sidecar | {field: value}), encoding="utf-8"
    )
    [info] = registry.databases()
    expected = {"title": "Sales", "description": "Orders."} | {
        field: "sales" if field == "title" else ""
    }
    assert (info.title, info.description, info.examples) == (
        expected["title"],
        expected["description"],
        ["Top?"],
    )


@pytest.mark.parametrize(
    "name",
    [
        "Upper.sqlite",
        "a.b.sqlite",
        "with space.sqlite",
        ".sqlite",
        "x.SQLITE",
        "x.db",
        "x.sqlite3",
        "x.sqlite-journal",
        "x.sqlite-wal",
        "x.sqlite-shm",
        "x.sqlite.part",
    ],
)
def test_other_file_names_are_ignored(
    folder: Path, registry: DatabaseRegistry, name: str
):
    make(folder, name)
    assert registry.databases() == []


def test_folders_are_ignored(folder: Path, registry: DatabaseRegistry):
    (folder / "nested.sqlite").mkdir()
    assert registry.databases() == []
    with pytest.raises(DatabaseNotFoundError):
        registry.get("nested")


@pytest.mark.parametrize("content", [b"", b"hello, not a database"])
def test_files_that_are_not_sqlite_are_ignored(
    folder: Path, registry: DatabaseRegistry, content: bytes
):
    (folder / "notes.sqlite").write_bytes(content)
    assert registry.databases() == []
    with pytest.raises(DatabaseNotFoundError):
        registry.get("notes")


@pytest.mark.parametrize(
    "damage",
    [header_only, header_and_garbage, cut_short],
    ids=["header-only", "garbage", "cut-short"],
)
def test_files_sqlite_cannot_open_are_left_out(
    folder: Path, registry: DatabaseRegistry, damage: Callable[[Path], None]
):
    make(folder, "good.sqlite")
    damage(folder / "broken.sqlite")
    assert ids(registry) == ["good"]
    with pytest.raises(DatabaseNotFoundError, match="broken"):
        registry.get("broken")
    assert registry.get("good").id == "good"


def test_a_file_that_is_still_being_copied_shows_up_when_complete(
    tmp_path: Path, folder: Path, registry: DatabaseRegistry
):
    complete = make(tmp_path, "complete.sqlite", tables=3).read_bytes()
    target = folder / "late.sqlite"
    target.write_bytes(complete[:4096])
    assert registry.databases() == []
    target.write_bytes(complete)
    assert ids(registry) == ["late"]
    assert registry.databases()[0].table_count == 3


def test_a_wal_database_is_listed(folder: Path, registry: DatabaseRegistry):
    wal_mode(folder / "wal.sqlite")
    make(folder, "good.sqlite")
    assert ids(registry) == ["good", "wal"]


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs a folder the test user cannot write to",
)
def test_a_wal_database_in_a_read_only_folder_is_left_out(
    folder: Path, registry: DatabaseRegistry
):
    wal_mode(folder / "wal.sqlite")
    make(folder, "good.sqlite")
    folder.chmod(0o555)
    try:
        assert ids(registry) == ["good"]
        with pytest.raises(DatabaseNotFoundError):
            registry.get("wal")
    finally:
        folder.chmod(0o755)


def test_not_found_does_not_name_the_server_folder(
    folder: Path, registry: DatabaseRegistry
):
    header_only(folder / "broken.sqlite")
    for database_id in ["nope", "broken"]:
        with pytest.raises(DatabaseNotFoundError) as raised:
            registry.get(database_id)
        assert database_id in str(raised.value)
        assert str(folder) not in str(raised.value)


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_symlink_to_a_file_outside_is_refused(
    tmp_path: Path, folder: Path, registry: DatabaseRegistry, relative: bool
):
    outside = make(tmp_path, "outside.sqlite")
    target = Path("..") / outside.name if relative else outside
    (folder / "leak.sqlite").symlink_to(target)
    assert registry.databases() == []
    with pytest.raises(DatabaseNotFoundError):
        registry.get("leak")


def test_symlink_to_a_file_inside_is_followed(folder: Path, registry: DatabaseRegistry):
    real = make(folder, "real.sqlite")
    (folder / "alias.sqlite").symlink_to(real.name)
    assert ids(registry) == ["alias", "real"]
    assert registry.get("alias").path == real.resolve()


def test_broken_symlink_is_ignored(folder: Path, registry: DatabaseRegistry):
    (folder / "gone.sqlite").symlink_to(folder / "missing.sqlite")
    assert registry.databases() == []


def test_symlinked_sidecar_outside_is_ignored(
    tmp_path: Path, folder: Path, registry: DatabaseRegistry
):
    make(folder, "sales.sqlite")
    outside = tmp_path / "secret.json"
    outside.write_text('{"title": "secret", "description": "secret"}', encoding="utf-8")
    (folder / "sales.json").symlink_to(outside)
    [info] = registry.databases()
    assert (info.title, info.description) == ("sales", "")


def test_a_symlinked_folder_is_used(tmp_path: Path, folder: Path):
    make(folder, "sales.sqlite")
    link = tmp_path / "link"
    link.symlink_to(folder, target_is_directory=True)
    assert ids(DatabaseRegistry(link)) == ["sales"]


def test_missing_folder_lists_nothing(tmp_path: Path):
    registry = DatabaseRegistry(tmp_path / "none")
    assert registry.databases() == []
    with pytest.raises(DatabaseNotFoundError, match="sales"):
        registry.get("sales")


def test_get_returns_a_listed_database(folder: Path, registry: DatabaseRegistry):
    path = make(folder, "sales.sqlite")
    database = registry.get("sales")
    assert database.id == "sales"
    assert database.path == path.resolve()


@pytest.mark.parametrize(
    "database_id",
    [
        "nope",
        "SALES",
        "sales.sqlite",
        "sales ",
        "sales\n",
        "",
        "../databases/sales",
        "./sales",
        "sub/../sales",
        "/sales",
    ],
)
def test_get_refuses_anything_but_a_listed_id(
    folder: Path, registry: DatabaseRegistry, database_id: str
):
    make(folder, "sales.sqlite")
    with pytest.raises(DatabaseNotFoundError, match="not found"):
        registry.get(database_id)


def test_get_refuses_an_absolute_path(folder: Path, registry: DatabaseRegistry):
    path = make(folder, "sales.sqlite")
    with pytest.raises(DatabaseNotFoundError):
        registry.get(str(path.with_suffix("")))


def test_files_copied_in_or_removed_later_are_seen(
    folder: Path, registry: DatabaseRegistry
):
    assert registry.databases() == []
    path = make(folder, "late.sqlite")
    assert ids(registry) == ["late"]
    assert registry.get("late").id == "late"
    path.unlink()
    assert registry.databases() == []
    with pytest.raises(DatabaseNotFoundError):
        registry.get("late")


def test_connections_are_read_only(folder: Path, registry: DatabaseRegistry):
    make(folder, "sales.sqlite")
    with registry.get("sales").connect() as connection:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        assert connection.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE) is True
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO t0 VALUES (1)")
