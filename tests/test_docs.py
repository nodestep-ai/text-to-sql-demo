import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REPOSITORY = "https://github.com/nodestep-ai/text-to-sql-demo/"
LINK = re.compile(r"\]\(([^)\s]+)\)")
REPOSITORY_PATH = re.compile(r"(?:blob|tree)/main/([^#]+)(?:#.*)?")
PAGES = sorted(DOCS.rglob("*.md"))
README_HEADER = (
    "<picture>\n"
    '  <source media="(prefers-color-scheme: dark)" srcset="assets/text-to-sql-demo-dark.svg">\n'
    '  <img src="assets/text-to-sql-demo.svg" alt="" width="56">\n'
    "</picture>\n\n# text-to-sql-demo\n"
)
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b")


def links(page: Path) -> list[str]:
    return LINK.findall(page.read_text(encoding="utf-8"))


README_SECTIONS = [
    "Setup",
    "Demo",
    "With a model",
    "Your own databases",
    "Memory",
    "Skills",
    "Sub-agents",
    "Tracing",
    "Docker",
    "Settings",
    "Safety",
    "API",
    "Development",
    "License",
]


def test_the_guide_is_the_readme_and_docs_holds_the_api_and_security_pages():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.findall(r"^## (.+)$", readme, flags=re.MULTILINE) == README_SECTIONS
    assert sorted(page.name for page in DOCS.glob("*.md")) == ["api.md", "security.md"]


def test_relative_links_in_the_readme_point_at_files_in_the_repository():
    for link in links(ROOT / "README.md"):
        if "://" in link or link.startswith("#"):
            continue
        assert (ROOT / link.partition("#")[0]).exists(), link


@pytest.mark.parametrize("page", PAGES, ids=lambda page: str(page.relative_to(DOCS)))
def test_relative_links_point_at_pages_or_images_inside_docs(page: Path):
    for link in links(page):
        if "://" in link or link.startswith("#"):
            continue
        target = (page.parent / link.partition("#")[0]).resolve()
        assert target.is_file(), link
        if target == ROOT / "README.md":
            continue
        assert target.is_relative_to(DOCS), link
        assert target.suffix in {".md", ".gif"}, link


@pytest.mark.parametrize("page", PAGES, ids=lambda page: str(page.relative_to(DOCS)))
def test_links_into_this_repository_point_at_files_on_main(page: Path):
    for link in links(page):
        if not link.startswith(REPOSITORY):
            continue
        match = REPOSITORY_PATH.fullmatch(link.removeprefix(REPOSITORY))
        assert match, link
        assert (ROOT / match[1]).exists(), link


def test_the_readme_opens_with_a_logo_for_each_theme_and_the_title():
    assert (ROOT / "README.md").read_text(encoding="utf-8").startswith(README_HEADER)


@pytest.mark.parametrize(
    ("logo", "ink"),
    [("text-to-sql-demo.svg", "#1f1b17"), ("text-to-sql-demo-dark.svg", "#ede9e4")],
)
def test_each_readme_logo_has_one_ink_and_the_amber_accent(logo: str, ink: str):
    text = (ROOT / "assets" / logo).read_text(encoding="utf-8")
    assert 'd="M14 50V14L50 50V14"' in text
    assert set(HEX_COLOR.findall(text)) == {ink, "#f5a524"}
    assert "prefers-color-scheme" not in text
