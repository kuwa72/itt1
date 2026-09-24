"""コントローラー契約テスト（Issue #7 由来、#39 で再生経路廃止に伴い更新）。

- add_to_playlist は "added" / "already_exists" / False を返す
- 追加対象トラックは iTunes 側の CurrentTrack ではなく dbid でライブラリ内を特定する
  （再生はローカルエンジンが担うため iTunes は再生中トラックを持たない。Issue #39）
- gui_tk.goto_current_track が ctrl.itunes 属性に直接依存しない
"""

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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
        lib_track = SimpleNamespace(TrackDatabaseID=1)
        target = _make_windows_target(search_result=SimpleNamespace(Count=0))
        ctrl._find_playlist_by_name = MagicMock(return_value=target)
        ctrl._find_library_track = MagicMock(return_value=lib_track)

        assert ctrl.add_to_playlist("PL", 1, "n") == "added"
        target.AddTrack.assert_called_once_with(lib_track)

    def test_already_exists_via_search(self):
        ctrl = _make_windows_ctrl()
        search = MagicMock(name="search")
        search.Count = 1
        search.Item.return_value = SimpleNamespace(TrackDatabaseID=1)
        target = _make_windows_target(search_result=search)
        ctrl._find_playlist_by_name = MagicMock(return_value=target)
        ctrl._find_library_track = MagicMock(
            return_value=SimpleNamespace(TrackDatabaseID=1)
        )

        assert ctrl.add_to_playlist("PL", 1, "n") == "already_exists"
        target.AddTrack.assert_not_called()

    def test_already_exists_via_track_scan(self):
        """曲名が無い場合はプレイリスト内全件走査で重複確認"""
        ctrl = _make_windows_ctrl()
        target = MagicMock(name="playlist")
        target.Search = None  # Search 無しの経路を通す
        target.Tracks = [SimpleNamespace(TrackDatabaseID=1)]
        ctrl._find_playlist_by_name = MagicMock(return_value=target)
        ctrl._find_library_track = MagicMock(
            return_value=SimpleNamespace(TrackDatabaseID=1)
        )

        assert ctrl.add_to_playlist("PL", 1, None) == "already_exists"
        target.AddTrack.assert_not_called()

    def test_false_when_no_itunes(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        assert ctrl.add_to_playlist("PL", 1, "n") is False

    def test_false_when_no_dbid(self):
        """ローカル再生中のトラックが無い（dbid 不明）場合は失敗"""
        ctrl = _make_windows_ctrl()
        assert ctrl.add_to_playlist("PL", None, "n") is False

    def test_false_when_playlist_not_found(self):
        ctrl = _make_windows_ctrl()
        ctrl._find_playlist_by_name = MagicMock(return_value=None)
        assert ctrl.add_to_playlist("PL", 1, "n") is False

    def test_false_when_track_not_in_library(self):
        ctrl = _make_windows_ctrl()
        ctrl._find_playlist_by_name = MagicMock(return_value=_make_windows_target())
        ctrl._find_library_track = MagicMock(return_value=None)
        assert ctrl.add_to_playlist("PL", 1, "n") is False


class TestFindLibraryTrack:
    def test_search_fast_path(self):
        ctrl = _make_windows_ctrl()
        hit = SimpleNamespace(TrackDatabaseID=42)
        res = MagicMock(name="search")
        res.Count = 1
        res.Item.return_value = hit
        ctrl.itunes.LibraryPlaylist.Search.return_value = res

        assert ctrl._find_library_track(42, "n") is hit

    def test_full_scan_fallback(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes.LibraryPlaylist.Search.return_value = SimpleNamespace(Count=0)
        miss = SimpleNamespace(TrackDatabaseID=1)
        hit = SimpleNamespace(TrackDatabaseID=42)
        ctrl.itunes.LibraryPlaylist.Tracks = [miss, hit]

        assert ctrl._find_library_track(42, "n") is hit

    def test_not_found(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes.LibraryPlaylist.Search.return_value = SimpleNamespace(Count=0)
        ctrl.itunes.LibraryPlaylist.Tracks = [SimpleNamespace(TrackDatabaseID=1)]
        assert ctrl._find_library_track(42, "n") is None


class TestMacOSAddToPlaylistContract:
    """macOS 側も bool ではなく "added"/"already_exists"/False を返すこと。"""

    def test_added(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="added"):
            assert ctrl.add_to_playlist("PL", 1) == "added"

    def test_already_exists(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="already_exists"):
            assert ctrl.add_to_playlist("PL", 1) == "already_exists"

    def test_error_result_returns_false(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="error"):
            assert ctrl.add_to_playlist("PL", 1) is False

    def test_exception_returns_false(self):
        ctrl = MacOSMusicController()
        with patch.object(
            ctrl, "_run_applescript", side_effect=RuntimeError("osascript failed")
        ):
            assert ctrl.add_to_playlist("PL", 1) is False

    def test_no_dbid_short_circuits(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript") as run:
            assert ctrl.add_to_playlist("PL") is False
        run.assert_not_called()


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
    app._latest_track_info = {}
    app._playback_playlist = None
    return app


def test_goto_current_track_does_not_touch_ctrl_itunes():
    """goto_current_track は ctrl.itunes を直接参照しない（macOS 互換）"""
    src = inspect.getsource(ITunesTkApp.goto_current_track)
    assert ".itunes" not in src


def test_goto_current_track_moves_to_current_playlist():
    app = _make_app()
    app._latest_track_info = {"dbid": 1, "playlist": "MyPL"}

    app.goto_current_track()

    app._select_playlist_in_listbox.assert_called_once_with("MyPL")
    app.load_tracks.assert_called_once_with("MyPL")


def test_goto_current_track_no_playlist_does_not_raise():
    app = _make_app()

    app.goto_current_track()  # 例外にならないこと

    app._select_playlist_in_listbox.assert_not_called()
    app.load_tracks.assert_not_called()


def test_itunes_controller_module_removed():
    """二重実装の itunes_controller.py は削除済み"""
    assert not (ROOT / "itunes_controller.py").exists()
    assert "itunes_controller" not in sys.modules


def test_tui_removed():
    """エントリポイントを持たない TUI は #39 の再生経路廃止に伴い削除済み"""
    assert not (ROOT / "tui_interface.py").exists()
    assert "tui_interface" not in sys.modules
