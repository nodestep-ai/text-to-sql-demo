import importlib.metadata

import text_to_sql_demo


def test_package_is_installed():
    assert importlib.metadata.version("text-to-sql-demo") == "0.1.0a1"


def test_version_follows_from_the_metadata():
    assert text_to_sql_demo.__version__ == importlib.metadata.version(
        "text-to-sql-demo"
    )
