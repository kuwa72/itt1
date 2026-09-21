"""Issue #23: 「表示」メニューのトグル整理テスト。

- 削除: プレイリスト一覧 / トラック一覧 / BPMパネルのチェックボタンと
  対応する BooleanVar / toggle メソッド（panes() 比較バグで機能していない死にコード）
- 削除: 画面内の「ヘルプ表示」チェックボタンと help_header フレーム
- 集約: 「表示」メニューに ヘルプ（既存 help_visible / toggle_help を流用）と
  ログ（新設 show_log / toggle_log で action_log_frame を pack/pack_forget 切替）
"""

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gui_tk import ITunesTkApp

ROOT = Path(__file__).resolve().parent.parent


def _gui_source() -> str:
    return (ROOT / "gui_tk.py").read_text(encoding="utf-8")


def _gui_tree() -> ast.AST:
    return ast.parse(_gui_source())


def _view_menu_checkbutton_labels() -> list[str]:
    """view_menu.add_checkbutton(label="...") の並び順を抽出する"""
    return re.findall(
        r'view_menu\.add_checkbutton\(label="([^"]+)"', _gui_source()
    )


def _init_self_attrs() -> set[str]:
    """ITunesTkApp.__init__ 内で self.<attr> = ... として代入される属性名を集める"""
    for node in ast.walk(_gui_tree()):
        if not (isinstance(node, ast.ClassDef) and node.name == "ITunesTkApp"):
            continue
        for sub in node.body:
            if isinstance(sub, ast.FunctionDef) and sub.name == "__init__":
                return {
                    a.attr
                    for a in ast.walk(sub)
                    if isinstance(a, ast.Attribute)
                    and isinstance(a.value, ast.Name)
                    and a.value.id == "self"
                    and isinstance(a.ctx, ast.Store)
                }
    raise AssertionError("ITunesTkApp.__init__ が見つからない")


# --- 「表示」メニュー最終構成 ---

def test_view_menu_items_in_order():
    """「表示」メニューは プログレスバー/クイックスロット/ログ/ヘルプ の順"""
    assert _view_menu_checkbutton_labels() == [
        "プログレスバー",
        "クイックスロット",
        "ログ",
        "ヘルプ",
    ]


def test_view_menu_removed_items_absent():
    """削除対象のチェックボタンがメニューに残っていない"""
    labels = _view_menu_checkbutton_labels()
    for removed in ("プレイリスト一覧", "トラック一覧", "BPMパネル"):
        assert removed not in labels


def test_view_menu_help_uses_existing_var_and_command():
    """ヘルプのチェックボタンは既存の help_visible / toggle_help を流用する"""
    src = _gui_source()
    m = re.search(
        r'view_menu\.add_checkbutton\(label="ヘルプ"[^)]*\)', src
    )
    assert m, "「ヘルプ」チェックボタンが view_menu に無い"
    assert "variable=self.help_visible" in m.group(0)
    assert "command=self.toggle_help" in m.group(0)


def test_view_menu_log_uses_show_log_and_toggle_log():
    """ログのチェックボタンは show_log / toggle_log に接続される"""
    src = _gui_source()
    m = re.search(
        r'view_menu\.add_checkbutton\(label="ログ"[^)]*\)', src
    )
    assert m, "「ログ」チェックボタンが view_menu に無い"
    assert "variable=self.show_log" in m.group(0)
    assert "command=self.toggle_log" in m.group(0)


# --- 死にコード削除の契約 ---

@pytest.mark.parametrize(
    "name",
    [
        "show_playlists",
        "show_tracks",
        "show_bpm",
        "toggle_playlists",
        "toggle_tracks",
        "toggle_bpm",
        "help_header",
    ],
)
def test_dead_code_removed(name):
    assert name not in _gui_source(), f"{name} が gui_tk.py に残っている"


# --- BooleanVar の初期化位置 ---

def test_help_visible_initialized_in_init():
    """help_visible は build_ui のメニュー構築より前（__init__）で初期化される"""
    assert "help_visible" in _init_self_attrs()


def test_show_log_initialized_in_init():
    assert "show_log" in _init_self_attrs()


# --- toggle_log の振る舞い ---

def _make_app(show_log_value: bool) -> ITunesTkApp:
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.show_log = MagicMock(name="show_log")
    app.show_log.get.return_value = show_log_value
    app.action_log_frame = MagicMock(name="action_log_frame")
    return app


def test_toggle_log_shows_frame_when_on():
    app = _make_app(True)
    app.toggle_log()
    app.action_log_frame.pack.assert_called_once()
    app.action_log_frame.pack_forget.assert_not_called()


def test_toggle_log_hides_frame_when_off():
    app = _make_app(False)
    app.toggle_log()
    app.action_log_frame.pack_forget.assert_called_once()
    app.action_log_frame.pack.assert_not_called()
