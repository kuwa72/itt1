"""Issue #42: プレイリスト操作（名前変更/削除/フォルダ作成/フォルダ移動）のテスト。

- WindowsMusicController: rename_playlist / delete_playlist / create_folder /
  move_playlist_to_folder の COM 呼出し経路
- MacOSMusicController: 同等の AppleScript メソッド
- gui_tk: プレイリスト一覧の右クリックメニューが _com_submit 経由で
  COM タスクを投入し、結果が _handle_task_result でログ枠＋一覧再読込へ反映される
"""

import logging
import queue
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import gui_tk
from gui_tk import ITunesTkApp
from music_controller_base import MacOSMusicController
from music_controller_windows import WindowsMusicController


def _make_windows_ctrl():
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = MagicMock(name="itunes")
    return ctrl


# --- Windows コントローラー ---

class TestWindowsRenamePlaylist:
    def test_rename_sets_name(self):
        ctrl = _make_windows_ctrl()
        target = MagicMock(name="playlist")
        ctrl._find_playlist_by_name = MagicMock(return_value=target)

        assert ctrl.rename_playlist("old", "new") is True
        assert target.Name == "new"

    def test_rename_not_found_returns_false(self, caplog):
        ctrl = _make_windows_ctrl()
        ctrl._find_playlist_by_name = MagicMock(return_value=None)
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.rename_playlist("missing", "new") is False
        assert "missing" in caplog.text

    def test_rename_disconnected_returns_false(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        assert ctrl.rename_playlist("old", "new") is False

    def test_rename_com_error_returns_false(self, caplog):
        ctrl = _make_windows_ctrl()
        target = MagicMock(name="playlist")
        type(target).Name = property(
            lambda self: "old",
            lambda self, v: (_ for _ in ()).throw(RuntimeError("readonly")),
        )
        ctrl._find_playlist_by_name = MagicMock(return_value=target)
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.rename_playlist("old", "new") is False
        assert "readonly" in caplog.text


class TestWindowsDeletePlaylist:
    def test_delete_calls_delete(self):
        ctrl = _make_windows_ctrl()
        target = MagicMock(name="playlist")
        ctrl._find_playlist_by_name = MagicMock(return_value=target)

        assert ctrl.delete_playlist("PL") is True
        target.Delete.assert_called_once_with()

    def test_delete_not_found_returns_false(self, caplog):
        ctrl = _make_windows_ctrl()
        ctrl._find_playlist_by_name = MagicMock(return_value=None)
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.delete_playlist("missing") is False
        assert "missing" in caplog.text

    def test_delete_disconnected_returns_false(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        assert ctrl.delete_playlist("PL") is False

    def test_delete_com_error_returns_false(self, caplog):
        ctrl = _make_windows_ctrl()
        target = MagicMock(name="playlist")
        target.Delete.side_effect = RuntimeError("boom")
        ctrl._find_playlist_by_name = MagicMock(return_value=target)
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.delete_playlist("PL") is False
        assert "boom" in caplog.text


class TestWindowsCreateFolder:
    def test_create_folder_calls_itunes(self):
        """IITSource は CreateFolder を公開していない（実機検証済み）ため
        IiTunes.CreateFolder でメインライブラリ直下に作る"""
        ctrl = _make_windows_ctrl()

        assert ctrl.create_folder("fld") is True
        ctrl.itunes.CreateFolder.assert_called_once_with("fld")

    def test_create_folder_disconnected_returns_false(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        assert ctrl.create_folder("fld") is False

    def test_create_folder_com_error_returns_false(self, caplog):
        ctrl = _make_windows_ctrl()
        ctrl.itunes.CreateFolder.side_effect = RuntimeError("boom")
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.create_folder("fld") is False
        assert "boom" in caplog.text


class TestWindowsMovePlaylistToFolder:
    def test_move_sets_parent(self):
        """IITUserPlaylist.Parent は settable（実機検証済み）。
        playlist.Parent = folder でフォルダ内へ移動できる"""
        ctrl = _make_windows_ctrl()
        playlist = MagicMock(name="playlist")
        folder = MagicMock(name="folder")

        def _find(name):
            return {"PL": playlist, "fld": folder}.get(name)

        ctrl._find_playlist_by_name = MagicMock(side_effect=_find)
        ctrl._is_folder_playlist = MagicMock(
            side_effect=lambda p: p is folder
        )

        assert ctrl.move_playlist_to_folder("PL", "fld") is True
        assert playlist.Parent is folder

    def test_move_rejects_non_folder_target(self, caplog):
        ctrl = _make_windows_ctrl()
        playlist = MagicMock(name="playlist")
        not_folder = MagicMock(name="not_folder")
        ctrl._find_playlist_by_name = MagicMock(
            side_effect=lambda n: {"PL": playlist, "x": not_folder}.get(n)
        )
        ctrl._is_folder_playlist = MagicMock(return_value=False)
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.move_playlist_to_folder("PL", "x") is False
        playlist.Parent = not_folder  # 呼ばれても失敗する前提だが代入自体は許容
        assert "x" in caplog.text

    def test_move_not_found_returns_false(self):
        ctrl = _make_windows_ctrl()
        ctrl._find_playlist_by_name = MagicMock(return_value=None)
        assert ctrl.move_playlist_to_folder("PL", "fld") is False

    def test_move_disconnected_returns_false(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        assert ctrl.move_playlist_to_folder("PL", "fld") is False


# --- macOS コントローラー ---

class TestMacOSPlaylistOps:
    def _make_mac_ctrl(self):
        ctrl = MacOSMusicController.__new__(MacOSMusicController)
        ctrl._run_applescript = MagicMock(return_value="")
        return ctrl

    def test_rename_runs_applescript(self):
        ctrl = self._make_mac_ctrl()
        assert ctrl.rename_playlist("old", "new") is True
        script = ctrl._run_applescript.call_args[0][0]
        assert "set name of user playlist" in script
        assert "old" in script and "new" in script

    def test_delete_runs_applescript(self):
        ctrl = self._make_mac_ctrl()
        assert ctrl.delete_playlist("PL") is True
        script = ctrl._run_applescript.call_args[0][0]
        assert "delete user playlist" in script
        assert "PL" in script

    def test_create_folder_runs_applescript(self):
        ctrl = self._make_mac_ctrl()
        assert ctrl.create_folder("fld") is True
        script = ctrl._run_applescript.call_args[0][0]
        assert "folder playlist" in script
        assert "fld" in script

    def test_move_to_folder_runs_applescript(self):
        ctrl = self._make_mac_ctrl()
        assert ctrl.move_playlist_to_folder("PL", "fld") is True
        script = ctrl._run_applescript.call_args[0][0]
        assert "PL" in script and "fld" in script

    def test_rename_applescript_error_returns_false(self):
        ctrl = self._make_mac_ctrl()
        ctrl._run_applescript.side_effect = RuntimeError("osascript fail")
        assert ctrl.rename_playlist("old", "new") is False


# --- GUI ---

def _make_app(with_worker=True):
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.bank_size = 36
    app.slot_bank = 0
    app.quick_slots = []
    app.slot_labels = []
    app._slot_default_bg = "#000000"
    app._slot_default_fg = "#ffffff"
    app._ui_queue = queue.Queue()
    app._com_task_queue = queue.Queue() if with_worker else None
    app._closing = False
    app.last_action = ""
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app.ctrl = MagicMock(name="ctrl")
    app.root = MagicMock(name="root")
    app._after = MagicMock(name="_after")
    app.playlist_listbox = MagicMock(name="playlist_listbox")
    app.playlist_listbox.curselection.return_value = (0,)
    app._playlist_raw_names = ["PL-A", "PL-B"]
    app.load_playlists = MagicMock(name="load_playlists")
    return app


class TestPlaylistContextMenu:
    def test_right_click_selects_item_and_posts_menu(self):
        app = _make_app()
        app.playlist_listbox.nearest.return_value = 1
        app.playlist_listbox.size.return_value = 2
        event = SimpleNamespace(y=10, x_root=5, y_root=5)

        with patch.object(gui_tk.tk, "Menu") as menu_cls:
            menu = menu_cls.return_value
            app._on_playlist_context_menu(event)

        app.playlist_listbox.selection_set.assert_called_once_with(1)
        menu.tk_popup.assert_called_once_with(5, 5)
        menu.grab_release.assert_called_once_with()

    def test_selected_playlist_name_uses_raw_names(self):
        """フォルダ/名前 形式の表示ラベルではなく生名を返す"""
        app = _make_app()
        app.playlist_listbox.curselection.return_value = (1,)
        assert app._selected_playlist_name() == "PL-B"

    def test_selected_playlist_name_no_selection(self):
        app = _make_app()
        app.playlist_listbox.curselection.return_value = ()
        assert app._selected_playlist_name() is None


class TestRenameSelectedPlaylist:
    def test_submits_rename_task(self):
        app = _make_app()
        with patch("tkinter.simpledialog.askstring", return_value="new-name"):
            app.rename_selected_playlist()

        app.ctrl.rename_playlist.assert_not_called()
        fn, args, kwargs, result_tag = app._com_task_queue.get_nowait()
        assert fn == "rename_playlist"
        assert args == ("PL-A", "new-name")
        assert result_tag[0] == "playlist_rename"

    def test_cancel_submits_nothing(self):
        app = _make_app()
        with patch("tkinter.simpledialog.askstring", return_value=None):
            app.rename_selected_playlist()
        assert app._com_task_queue.empty()
        assert "キャンセル" in app.last_action

    def test_no_selection_submits_nothing(self):
        app = _make_app()
        app.playlist_listbox.curselection.return_value = ()
        app.rename_selected_playlist()
        assert app._com_task_queue.empty()

    def test_result_success_logs_and_reloads(self):
        app = _make_app()
        app._handle_task_result(("playlist_rename", "PL-A", "new-name"), True)
        assert "new-name" in app.last_action
        app.load_playlists.assert_called_once_with()

    def test_result_failure_logs_no_reload(self):
        app = _make_app()
        app._handle_task_result(("playlist_rename", "PL-A", "new-name"), False)
        assert "失敗" in app.last_action
        app.load_playlists.assert_not_called()


class TestDeleteSelectedPlaylist:
    def test_submits_delete_task_after_confirm(self):
        app = _make_app()
        with patch.object(gui_tk.messagebox, "askyesno", return_value=True):
            app.delete_selected_playlist()

        app.ctrl.delete_playlist.assert_not_called()
        fn, args, kwargs, result_tag = app._com_task_queue.get_nowait()
        assert fn == "delete_playlist"
        assert args == ("PL-A",)
        assert result_tag[0] == "playlist_delete"

    def test_cancel_submits_nothing(self):
        app = _make_app()
        with patch.object(gui_tk.messagebox, "askyesno", return_value=False):
            app.delete_selected_playlist()
        assert app._com_task_queue.empty()
        assert "キャンセル" in app.last_action

    def test_result_success_logs_and_reloads(self):
        app = _make_app()
        app._handle_task_result(("playlist_delete", "PL-A"), True)
        assert "PL-A" in app.last_action
        app.load_playlists.assert_called_once_with()

    def test_result_failure_logs_no_reload(self):
        app = _make_app()
        app._handle_task_result(("playlist_delete", "PL-A"), False)
        assert "失敗" in app.last_action
        app.load_playlists.assert_not_called()


class TestCreatePlaylistFolder:
    def test_submits_create_folder_task(self):
        app = _make_app()
        with patch("tkinter.simpledialog.askstring", return_value="fld"):
            app.create_playlist_folder()

        app.ctrl.create_folder.assert_not_called()
        fn, args, kwargs, result_tag = app._com_task_queue.get_nowait()
        assert fn == "create_folder"
        assert args == ("fld",)
        assert result_tag[0] == "playlist_create_folder"

    def test_cancel_submits_nothing(self):
        app = _make_app()
        with patch("tkinter.simpledialog.askstring", return_value=None):
            app.create_playlist_folder()
        assert app._com_task_queue.empty()

    def test_result_success_logs_and_reloads(self):
        app = _make_app()
        app._handle_task_result(("playlist_create_folder", "fld"), True)
        assert "fld" in app.last_action
        app.load_playlists.assert_called_once_with()

    def test_result_failure_logs_no_reload(self):
        app = _make_app()
        app._handle_task_result(("playlist_create_folder", "fld"), False)
        assert "失敗" in app.last_action
        app.load_playlists.assert_not_called()


class TestWorkerControllerParity:
    """COM ワーカー側コントローラー（Dispatch 済み前提）でも
    追加メソッドが同じ名前で呼べること（_com_submit は fn 名文字列で解決する）"""

    @pytest.mark.parametrize("method", [
        "rename_playlist",
        "delete_playlist",
        "create_folder",
        "move_playlist_to_folder",
    ])
    @pytest.mark.parametrize("cls", [WindowsMusicController, MacOSMusicController])
    def test_method_exposed_on_both_platforms(self, cls, method):
        assert callable(getattr(cls, method, None))
