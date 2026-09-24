"""Issue #41: ウィンドウジオメトリ・レイアウト改善のテスト。

- 初期ウィンドウサイズ: クイックスロット（36ボタン/バンク、高さ固定 Label 行）
  が初期表示で収まる大きさになっていること
- レイアウト順: build_ui の pack 順が track → paned → スロット → ヘルプ → ログ
  （ログ枠が最下部）であること
- トグルバグ: pack_forget → pack で再表示しても末尾へ移動しないよう、
  再 pack 時に `before=` で元の位置へ固定すること
- geometry 永続化: on_closing で config.json へ保存し、起動時に復元すること
  （ConfigManager.get/set_window_geometry 経由）
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config import AppConfig, ConfigManager
from gui_tk import ITunesTkApp

ROOT = Path(__file__).resolve().parent.parent


def _gui_source() -> str:
    return (ROOT / "gui_tk.py").read_text(encoding="utf-8")


def _build_ui_source() -> str:
    """build_ui メソッドのソースだけを切り出す（トグル内の同名 pack を除外するため）"""
    src = _gui_source()
    start = src.index("def build_ui")
    end = src.index("def bind_keys")
    return src[start:end]


# --- 初期ウィンドウサイズ ---

def _parse_geometry(geom: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)x(\d+)(?:[+-]\d+){0,2}", geom)
    assert m, f"不正な geometry 文字列: {geom}"
    return int(m.group(1)), int(m.group(2))


def test_default_geometry_fits_quick_slots():
    """初期 geometry がクイックスロット枠込みで収まる大きさ"""
    w, h = _parse_geometry(ITunesTkApp.DEFAULT_GEOMETRY)
    # 13列×高さ固定Label3行+ログ5行+トラック枠を初期表示で収める目安
    assert w >= 960
    assert h >= 840


def test_minsize_keeps_slots_visible():
    """minsize でもスロット枠が欠けない下限を保証"""
    w, h = ITunesTkApp.MIN_WINDOW_SIZE
    assert w >= 880
    assert h >= 740


def test_init_applies_saved_geometry_via_initial_geometry():
    """__init__ は _initial_geometry() の結果を root.geometry に渡す"""
    src = _gui_source()
    init_start = src.index("def __init__")
    init_end = src.index("def build_ui")
    init_src = src[init_start:init_end]
    assert "self.root.geometry(self._initial_geometry())" in init_src
    assert "self.root.minsize(*self.MIN_WINDOW_SIZE)" in init_src


# --- build_ui の pack 順（ログが最下部） ---

def test_build_ui_pack_order_log_is_last():
    """build_ui 内の pack 順は スロット → ヘルプ → ログ"""
    src = _build_ui_source()
    i_slots = src.index("self.slots_frame.pack(")
    i_help = src.index("self.help_frame.pack(")
    i_log = src.index("self.action_log_frame.pack(")
    assert i_slots < i_help < i_log


def test_build_ui_log_frame_created_after_middle_paned():
    """ログ枠は middle_paned より後に作られ最下段に置かれる"""
    src = _build_ui_source()
    i_paned = src.index("self.middle_paned.pack(")
    i_log = src.index("self.action_log_frame.pack(")
    assert i_paned < i_log


# --- トグル時の位置固定 ---

def _make_app(slots_on: bool, log_on: bool, help_on: bool,
              managed: dict | None = None) -> ITunesTkApp:
    """トグル系テスト用のアプリ。managed: 枠名→winfo_manager() の戻り値"""
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.show_slots = MagicMock(name="show_slots")
    app.show_slots.get.return_value = slots_on
    app.show_log = MagicMock(name="show_log")
    app.show_log.get.return_value = log_on
    app.help_visible = MagicMock(name="help_visible")
    app.help_visible.get.return_value = help_on
    app.slots_frame = MagicMock(name="slots_frame")
    app.help_frame = MagicMock(name="help_frame")
    app.action_log_frame = MagicMock(name="action_log_frame")
    managed = managed or {}
    for name, frame in (
        ("slots_frame", app.slots_frame),
        ("help_frame", app.help_frame),
        ("action_log_frame", app.action_log_frame),
    ):
        frame.winfo_manager.return_value = managed.get(name, "pack")
    return app


def test_toggle_slots_repacks_before_help_frame():
    """スロット再表示は直下のヘルプ枠の前へ挿入される（末尾へ行かない）"""
    app = _make_app(True, True, True)
    app.toggle_slots()
    app.slots_frame.pack.assert_called_once()
    assert app.slots_frame.pack.call_args.kwargs.get("before") is app.help_frame


def test_toggle_slots_falls_back_to_log_frame_when_help_unmanaged():
    """ヘルプ非表示中はスロットをログ枠の前へ挿入する"""
    app = _make_app(True, True, False, managed={"help_frame": ""})
    app.toggle_slots()
    assert app.slots_frame.pack.call_args.kwargs.get("before") is app.action_log_frame


def test_toggle_slots_packs_plain_when_no_successor_managed():
    """後続枠がすべて非表示なら before 無しで pack する"""
    app = _make_app(True, False, False,
                    managed={"help_frame": "", "action_log_frame": ""})
    app.toggle_slots()
    assert "before" not in app.slots_frame.pack.call_args.kwargs


def test_toggle_slots_hides_when_off():
    app = _make_app(False, True, True)
    app.toggle_slots()
    app.slots_frame.pack_forget.assert_called_once()
    app.slots_frame.pack.assert_not_called()


def test_toggle_help_repacks_before_log_frame():
    """ヘルプ再表示はログ枠の前へ挿入される（ログは常に最下部）"""
    app = _make_app(True, True, True)
    app.toggle_help()
    app.help_frame.pack.assert_called_once()
    assert app.help_frame.pack.call_args.kwargs.get("before") is app.action_log_frame


def test_toggle_help_packs_plain_when_log_unmanaged():
    app = _make_app(True, False, True, managed={"action_log_frame": ""})
    app.toggle_help()
    assert "before" not in app.help_frame.pack.call_args.kwargs


def test_toggle_help_hides_when_off():
    app = _make_app(True, True, False)
    app.toggle_help()
    app.help_frame.pack_forget.assert_called_once()
    app.help_frame.pack.assert_not_called()


def test_toggle_log_repacks_at_bottom_without_before():
    """ログは最下段なので before 指定なしの pack で末尾に戻る"""
    app = _make_app(True, True, True)
    app.toggle_log()
    app.action_log_frame.pack.assert_called_once()
    assert "before" not in app.action_log_frame.pack.call_args.kwargs


# --- geometry の保存/復元 ---

def _make_app_for_geometry(saved: str | None) -> ITunesTkApp:
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.config = MagicMock(name="config")
    app.config.get_window_geometry.return_value = saved
    return app


def test_initial_geometry_restores_saved_value():
    app = _make_app_for_geometry("1200x900+10+20")
    assert app._initial_geometry() == "1200x900+10+20"


def test_initial_geometry_falls_back_when_empty():
    app = _make_app_for_geometry("")
    assert app._initial_geometry() == ITunesTkApp.DEFAULT_GEOMETRY


def test_initial_geometry_falls_back_when_none():
    app = _make_app_for_geometry(None)
    assert app._initial_geometry() == ITunesTkApp.DEFAULT_GEOMETRY


def test_initial_geometry_rejects_invalid_string():
    """壊れた config 値をそのまま geometry() に渡さない"""
    app = _make_app_for_geometry("wide x tall")
    assert app._initial_geometry() == ITunesTkApp.DEFAULT_GEOMETRY


def _make_app_for_closing(geometry: str) -> ITunesTkApp:
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.root = MagicMock(name="root")
    app.root.geometry.return_value = geometry
    app.config = MagicMock(name="config")
    return app


def test_on_closing_saves_geometry_to_config():
    app = _make_app_for_closing("1100x800+5+5")
    app.on_closing()
    app.config.set_window_geometry.assert_called_once_with("1100x800+5+5")


def test_on_closing_skips_save_when_geometry_empty():
    app = _make_app_for_closing("")
    app.on_closing()
    app.config.set_window_geometry.assert_not_called()


# --- ConfigManager の geometry 永続化 ---

def test_window_geometry_field_exists_with_default():
    assert AppConfig().window_geometry == ""


def test_window_geometry_roundtrip(tmp_path):
    cfg_file = tmp_path / "config.json"
    cm = ConfigManager(str(cfg_file))
    cm.set_window_geometry("1200x800+3+4")

    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["window_geometry"] == "1200x800+3+4"

    cm2 = ConfigManager(str(cfg_file))
    assert cm2.get_window_geometry() == "1200x800+3+4"


def test_window_geometry_default_when_missing(tmp_path):
    cm = ConfigManager(str(tmp_path / "nonexistent.json"))
    assert cm.get_window_geometry() == ""
