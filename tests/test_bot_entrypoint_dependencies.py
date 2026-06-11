import ast
from pathlib import Path

from app.core.version import APP_VERSION


def test_bot_entrypoint_does_not_import_main_module():
    bot_main_path = Path("app/bot/bot_main.py")
    tree = ast.parse(bot_main_path.read_text(encoding="utf-8"))

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "main" not in imported_modules


def test_app_version_constant_matches_fastapi_version():
    main_path = Path("main.py")
    tree = ast.parse(main_path.read_text(encoding="utf-8"))

    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.version"
        for alias in node.names
    }
    version_keywords = [
        keyword.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "FastAPI"
        for keyword in node.keywords
        if keyword.arg == "version" and isinstance(keyword.value, ast.Name)
    ]

    assert "APP_VERSION" in imported_names
    assert version_keywords == ["APP_VERSION"]
