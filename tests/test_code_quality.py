"""ソースレベルの品質チェック。

tkinter 等の GUI 依存が無い環境でも import せず AST で検証できるものを集約。
"""

import ast
import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

TRACKED_PY = [
    "main.py",
    "config.py",
    "gui_tk.py",
    "music_controller_base.py",
    "music_controller_windows.py",
    "tui_interface.py",
]


def _tree(filename: str) -> ast.Module:
    return ast.parse((ROOT / filename).read_text(encoding="utf-8"))


def _self_assigned_attrs(tree: ast.AST) -> set[str]:
    attrs: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and isinstance(node.ctx, ast.Store)
        ):
            attrs.add(node.attr)
    return attrs


@pytest.mark.parametrize("filename", TRACKED_PY)
def test_py_compile(filename, tmp_path):
    py_compile.compile(
        str(ROOT / filename),
        cfile=str(tmp_path / f"{filename}.pyc"),
        doraise=True,
    )


def test_main_has_no_run_app_gui_only():
    funcs = {
        n.name
        for n in ast.walk(_tree("main.py"))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "run_app_gui_only" not in funcs


def test_gui_has_no_bare_except():
    for node in ast.walk(_tree("gui_tk.py")):
        if isinstance(node, ast.ExceptHandler):
            assert node.type is not None, (
                f"裸の except: が残っている: line {node.lineno}"
            )


def test_gui_dead_fields_removed():
    """初期化のみで未使用のフィールドと、未更新のラベルが削除されていること"""
    assigned = _self_assigned_attrs(_tree("gui_tk.py"))
    for dead in (
        "_fetching_track_playlists",
        "_warming_cache",
        "_loading_indicator_after_id",
        "_last_track_sig",
        "_last_playlists_of_track",
        "track_in_playlists",
    ):
        assert dead not in assigned, f"self.{dead} は未使用のため削除されているはず"
