"""Issue #7: コントローラー二重実装の統合と add_to_playlist 戻り値契約の統一テスト。

- 全実装の add_to_playlist が "added" / "already_exists" / False を返す
- get_current_playlist_name() が Windows/macOS 双方に存在し、
  gui_tk.goto_current_track が ctrl.itunes 属性に直接依存しない
- tui_interface が共通ファクトリ create_music_controller() を使い、
  itunes_controller.py を参照しない
"""

import ast
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import tui_interface
from gui_tk import ITunesTkApp
from music_controller_base import MacOSMusicController
from music_controller_windows import WindowsMusicController

ROOT = Path(__file__).resolve().parent.parent


# --- add_to_playlist 戻り値契約 ---

def _make_windows_ctrl() -> WindowsMusicController:
    """__init__（COM接続）を通さず self.itunes だけ注入したインスタンスを返す。"""
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = MagicMock(name="itunes")
    return ctrl


def _make_windows_target(search_result=None):
    """Search/AddTrack を持つプレイリストのモックを返す。"""
    target = MagicMock(name="playlist")
    target.Search.return_value = search_result
    return target


class TestWindowsAddToPlaylistContract:
    def test_added(self):
        ctrl = _make_windows_ctrl()
        track = SimpleNamespace(Name="n", TrackDatabaseID=1)
        ctrl.itunes.CurrentTrack = track
        target = _make_windows_target(search_result=SimpleNamespace(Count=0))
        ctrl._find_playlist_by_name = MagicMock(return_value=target)

        assert ctrl.add_to_playlist("PL") == "added"
        target.AddTrack.assert_called_once_with(track)

    def test_already_exists(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes.CurrentTrack = SimpleNamespace(Name="n", TrackDatabaseID=1)
        search = MagicMock(name="search")
        search.Count = 1
        search.Item.return_value = SimpleNamespace(TrackDatabaseID=1)
        target = _make_windows_target(search_result=search)
        ctrl._find_playlist_by_name = MagicMock(return_value=target)

        assert ctrl.add_to_playlist("PL") == "already_exists"
        target.AddTrack.assert_not_called()

    def test_false_when_no_itunes(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        assert ctrl.add_to_playlist("PL") is False

    def test_false_when_no_current_track(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes.CurrentTrack = None
        assert ctrl.add_to_playlist("PL") is False

    def test_false_when_playlist_not_found(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes.CurrentTrack = SimpleNamespace(Name="n", TrackDatabaseID=1)
        ctrl._find_playlist_by_name = MagicMock(return_value=None)
        assert ctrl.add_to_playlist("PL") is False


class TestMacOSAddToPlaylistContract:
    """macOS 側も bool ではなく "added"/"already_exists"/False を返すこと。

    修正前は `result in [...]` の bool を返していたため、
    GUI が "added"/"already_exists" を期待する場面で常に失敗表示になっていた。
    """

    def test_added(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="added"):
            assert ctrl.add_to_playlist("PL") == "added"

    def test_already_exists(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="already_exists"):
            assert ctrl.add_to_playlist("PL") == "already_exists"

    def test_error_result_returns_false(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="error"):
            assert ctrl.add_to_playlist("PL") is False

    def test_exception_returns_false(self):
        ctrl = MacOSMusicController()
        with patch.object(
            ctrl, "_run_applescript", side_effect=RuntimeError("osascript failed")
        ):
            assert ctrl.add_to_playlist("PL") is False


# --- get_current_playlist_name 共通API ---

@pytest.mark.parametrize("cls", [WindowsMusicController, MacOSMusicController])
def test_get_current_playlist_name_exists(cls):
    assert callable(getattr(cls, "get_current_playlist_name", None)), (
        f"{cls.__name__}.get_current_playlist_name が存在しない"
    )


class TestWindowsGetCurrentPlaylistName:
    def test_returns_current_playlist_name(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes.CurrentPlaylist = SimpleNamespace(Name="MyPL")
        assert ctrl.get_current_playlist_name() == "MyPL"

    def test_none_when_no_current_playlist(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes.CurrentPlaylist = None
        assert ctrl.get_current_playlist_name() is None

    def test_none_when_no_itunes(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        assert ctrl.get_current_playlist_name() is None


class TestMacOSGetCurrentPlaylistName:
    def test_returns_name(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="MyPL") as run:
            assert ctrl.get_current_playlist_name() == "MyPL"
        assert "current playlist" in run.call_args[0][0]

    def test_none_on_error(self):
        ctrl = MacOSMusicController()
        with patch.object(
            ctrl, "_run_applescript", side_effect=RuntimeError("no playlist")
        ):
            assert ctrl.get_current_playlist_name() is None


# --- gui_tk.goto_current_track ---

def _make_app() -> ITunesTkApp:
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.root = MagicMock(name="root")
    app.ctrl = MagicMock(name="ctrl")
    del app.ctrl.itunes  # macOS コントローラーには itunes 属性がない
    app._manual_playlist_view = False
    app.track_tree = MagicMock(name="track_tree")
    app.track_tree.get_children.return_value = ()
    app._select_playlist_in_listbox = MagicMock(name="_select_playlist_in_listbox")
    app.load_tracks = MagicMock(name="load_tracks")
    app.last_action = "-"
    return app


def test_goto_current_track_does_not_touch_ctrl_itunes():
    """goto_current_track は ctrl.itunes を直接参照しない（macOS 互換）"""
    src = inspect.getsource(ITunesTkApp.goto_current_track)
    assert ".itunes" not in src


def test_goto_current_track_moves_to_current_playlist():
    app = _make_app()
    app.ctrl.get_current_playlist_name.return_value = "MyPL"
    app.ctrl.get_current_track_info.return_value = {}

    app.goto_current_track()

    app._select_playlist_in_listbox.assert_called_once_with("MyPL")
    app.load_tracks.assert_called_once_with("MyPL")


def test_goto_current_track_no_playlist_does_not_raise():
    app = _make_app()
    app.ctrl.get_current_playlist_name.return_value = None

    app.goto_current_track()  # 例外にならないこと

    app._select_playlist_in_listbox.assert_not_called()
    app.load_tracks.assert_not_called()


# --- TUI の共通コントローラー統合 ---

def test_tui_does_not_import_itunes_controller():
    """tui_interface は itunes_controller を import しない"""
    tree = ast.parse((ROOT / "tui_interface.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "itunes_controller" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            assert "itunes_controller" not in (node.module or "")


def test_itunes_controller_module_removed():
    """二重実装の itunes_controller.py は削除済み"""
    assert not (ROOT / "itunes_controller.py").exists()
    assert "itunes_controller" not in sys.modules


def test_tui_uses_create_music_controller_factory():
    """iTunesTUI は共通ファクトリ経由でコントローラーを生成する（macOS でも動く）"""
    with patch.object(
        tui_interface, "create_music_controller", MagicMock(name="factory")
    ) as factory, patch.object(tui_interface, "ConfigManager") as cm_cls:
        cm = cm_cls.return_value
        cm.get_quick_slots.return_value = []
        cm.check_library_xml_exists.return_value = ("", False)
        cm.config.skip_seconds = 10
        app = tui_interface.iTunesTUI()

    factory.assert_called_once_with()
    assert app.itunes is factory.return_value


def _make_tui() -> "tui_interface.iTunesTUI":
    with patch.object(
        tui_interface, "create_music_controller", MagicMock(name="factory")
    ), patch.object(tui_interface, "ConfigManager") as cm_cls:
        cm = cm_cls.return_value
        cm.get_quick_slots.return_value = []
        cm.check_library_xml_exists.return_value = ("", False)
        cm.config.skip_seconds = 10
        app = tui_interface.iTunesTUI()
    return app


def test_tui_add_to_playlist_handles_added():
    app = _make_tui()
    app.config_playlists = ["PL"]
    app.itunes.add_to_playlist.return_value = "added"

    app.add_to_playlist(0)
    assert "追加" in app.last_action
    assert "失敗" not in app.last_action


def test_tui_add_to_playlist_handles_already_exists():
    """already_exists は失敗ではなく既存として扱う"""
    app = _make_tui()
    app.config_playlists = ["PL"]
    app.itunes.add_to_playlist.return_value = "already_exists"

    app.add_to_playlist(0)
    assert "失敗" not in app.last_action


def test_tui_add_to_playlist_handles_failure():
    app = _make_tui()
    app.config_playlists = ["PL"]
    app.itunes.add_to_playlist.return_value = False

    app.add_to_playlist(0)
    assert "失敗" in app.last_action


def test_tui_main_handles_controller_connection_failure(capsys):
    """起動時に iTunes 未起動で接続失敗しても、メッセージ表示後に正常終了する"""
    with patch.object(
        tui_interface, "create_music_controller",
        side_effect=RuntimeError("iTunes COMに接続できません"),
    ), patch.object(tui_interface, "ConfigManager"):
        with pytest.raises(SystemExit) as exc:
            tui_interface.main()

    assert exc.value.code == 0
    out = capsys.readouterr()
    assert "iTunes" in (out.out + out.err)
