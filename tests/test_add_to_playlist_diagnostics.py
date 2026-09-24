"""Issue #33: プレイリスト追加失敗の可視化テスト。

- add_to_slot が COM ワーカー経由で add_to_playlist を実行する（UIスレッド直叩きしない）
- ワーカー無し時は従来通り直接呼出しにフォールバックする
- add_to_playlist の各失敗経路で具体的な理由が logger に出力される
- WARNING 以上のログレコードが _ui_queue 経由でログ枠へ転送される
"""

import logging
import queue
import threading
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gui_tk import ITunesTkApp
from music_controller_windows import WindowsMusicController


def _make_app(with_worker=True):
    """ITunesTkApp.__init__ を通さず必要な属性だけ注入したインスタンスを返す。"""
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.bank_size = 36
    app.slot_bank = 0
    app.quick_slots = ["PL-A"]
    app.slot_labels = [MagicMock(name="slot_label")]
    app._slot_default_bg = "#000000"
    app._slot_default_fg = "#ffffff"
    app._ui_queue = queue.Queue()
    app._com_task_queue = queue.Queue() if with_worker else None
    app._closing = False
    app.last_action = ""
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app.ctrl = MagicMock(name="ctrl")
    app._current_track_meta = {"dbid": 1, "name": "n"}
    app.root = MagicMock(name="root")
    app._after = MagicMock(name="_after")
    return app


# --- 1. add_to_slot が COM ワーカー経由になる ---

class TestAddToSlotWorkerRouting:
    def test_submits_add_to_playlist_to_worker(self):
        """ワーカーがいる場合、add_to_slot は _com_task_queue に
        add_to_playlist タスクを投入し、ctrl を直接呼ばない"""
        app = _make_app(with_worker=True)

        app.add_to_slot(0)

        app.ctrl.add_to_playlist.assert_not_called()
        fn, args, kwargs, result_tag = app._com_task_queue.get_nowait()
        assert fn == "add_to_playlist"
        assert args == ("PL-A", 1, "n")
        assert result_tag[0] == "add_to_playlist"
        assert result_tag[1] == "PL-A"

    def test_result_tag_carries_widget_index(self):
        """バンク跨ぎでもフラッシュ対象スロットを追跡できるよう
        widget_idx (idx % bank_size) が result_tag に入る"""
        app = _make_app(with_worker=True)
        app.bank_size = 2
        app.quick_slots = ["A", "B", "C"]

        app.add_to_slot(2)  # widget_idx = 0

        _, _, _, result_tag = app._com_task_queue.get_nowait()
        assert result_tag == ("add_to_playlist", "C", 0)

    def test_fallback_to_direct_call_without_worker(self):
        """ワーカーが無い場合は従来通り ctrl.add_to_playlist を直接呼ぶ"""
        app = _make_app(with_worker=False)
        app.ctrl.add_to_playlist.return_value = "added"

        app.add_to_slot(0)

        app.ctrl.add_to_playlist.assert_called_once_with("PL-A", 1, "n")
        assert "追加: PL-A" in app.last_action

    def test_task_result_updates_log_and_flash(self):
        """ワーカーから返った結果がログとスロットフラッシュに反映される"""
        app = _make_app()
        app._flash_slot = MagicMock(name="_flash_slot")

        app._handle_task_result(("add_to_playlist", "PL-A", 0), "added")

        assert "追加: PL-A" in app.last_action
        app._flash_slot.assert_called_once_with(0, "success")

    def test_task_result_already_exists(self):
        app = _make_app()
        app._flash_slot = MagicMock(name="_flash_slot")

        app._handle_task_result(("add_to_playlist", "PL-A", 0), "already_exists")

        assert "既に存在: PL-A" in app.last_action
        app._flash_slot.assert_called_once_with(0, "warning")

    def test_task_result_failure(self):
        app = _make_app()
        app._flash_slot = MagicMock(name="_flash_slot")

        app._handle_task_result(("add_to_playlist", "PL-A", 0), False)

        assert "追加失敗: PL-A" in app.last_action
        app._flash_slot.assert_called_once_with(0, "error")


# --- 2. add_to_playlist の失敗理由ログ ---

def _make_windows_ctrl():
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = MagicMock(name="itunes")
    return ctrl


class TestAddToPlaylistFailureReasons:
    def test_logs_reason_when_itunes_disconnected(self, caplog):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.add_to_playlist("PL", 1, "n") is False
        assert "接続" in caplog.text or "itunes" in caplog.text.lower()

    def test_logs_reason_when_no_current_track(self, caplog):
        """dbid 無し（ローカル再生中のトラックが無い）→ 理由ログ付き False"""
        ctrl = _make_windows_ctrl()
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.add_to_playlist("PL", None) is False
        assert "トラック" in caplog.text

    def test_logs_reason_when_playlist_not_found(self, caplog):
        ctrl = _make_windows_ctrl()
        ctrl._find_library_track = MagicMock(
            return_value=SimpleNamespace(TrackDatabaseID=1)
        )
        ctrl._find_playlist_by_name = MagicMock(return_value=None)
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.add_to_playlist("PL", 1, "n") is False
        assert "PL" in caplog.text

    def test_logs_playlist_name_on_addtrack_error(self, caplog):
        ctrl = _make_windows_ctrl()
        target = MagicMock(name="playlist")
        target.Search.return_value = SimpleNamespace(Count=0)
        target.AddTrack.side_effect = RuntimeError("boom")
        ctrl._find_playlist_by_name = MagicMock(return_value=target)
        ctrl._find_library_track = MagicMock(
            return_value=SimpleNamespace(TrackDatabaseID=1)
        )
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.add_to_playlist("PL", 1, "n") is False
        assert "PL" in caplog.text
        assert "boom" in caplog.text

    def test_find_playlist_logs_enumeration_error(self, caplog):
        """_find_playlist_by_name が列挙中の例外を握りつぶさずログに出す"""
        ctrl = _make_windows_ctrl()
        type(ctrl.itunes).Sources = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("enum fail"))
        )
        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl._find_playlist_by_name("PL") is None
        assert "enum fail" in caplog.text


# --- 4. makepy 静的ラッパー（gen_py）環境での AddTrack ---

class TestAddTrackMakepyFallback:
    """PyInstaller exe では gen_py の静的ラッパーが有効になり、
    Playlists.Item() が基底 IITPlaylist 型を返すため AddTrack が見えない。
    その場合 IITUserPlaylist へ CastTo してから AddTrack を呼ぶ（実機確認済み）。"""

    def _make_base_playlist(self):
        """IITPlaylist 基底型を模倣: AddTrack を持たないプレイリスト"""
        target = MagicMock(spec=["Name", "Tracks", "Search", "Play"])
        target.Search.return_value = SimpleNamespace(Count=0)
        return target

    def test_castto_fallback_on_attributeerror(self):
        ctrl = _make_windows_ctrl()
        lib_track = SimpleNamespace(TrackDatabaseID=1)
        target = self._make_base_playlist()
        ctrl._find_playlist_by_name = MagicMock(return_value=target)
        ctrl._find_library_track = MagicMock(return_value=lib_track)
        casted = MagicMock(name="IITUserPlaylist")

        with patch(
            "music_controller_windows.win32com.client.CastTo",
            return_value=casted,
        ) as cast:
            assert ctrl.add_to_playlist("PL", 1, "n") == "added"

        cast.assert_called_once_with(target, "IITUserPlaylist")
        casted.AddTrack.assert_called_once_with(lib_track)

    def test_castto_failure_returns_false(self, caplog):
        """CastTo 自体が失敗する対象（フォルダ等）は False + 理由ログ"""
        ctrl = _make_windows_ctrl()
        target = self._make_base_playlist()
        ctrl._find_playlist_by_name = MagicMock(return_value=target)
        ctrl._find_library_track = MagicMock(
            return_value=SimpleNamespace(TrackDatabaseID=1)
        )

        with patch(
            "music_controller_windows.win32com.client.CastTo",
            side_effect=RuntimeError("not a user playlist"),
        ):
            with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
                assert ctrl.add_to_playlist("PL", 1, "n") is False
        assert "PL" in caplog.text

    def test_addtrack_com_error_logged(self, caplog):
        """AddTrack 呼出し自体の失敗（スマートプレイリスト等）は理由ログ付き False"""
        ctrl = _make_windows_ctrl()
        target = MagicMock(name="playlist")
        target.Search.return_value = SimpleNamespace(Count=0)
        target.AddTrack.side_effect = RuntimeError("read only playlist")
        ctrl._find_playlist_by_name = MagicMock(return_value=target)
        ctrl._find_library_track = MagicMock(
            return_value=SimpleNamespace(TrackDatabaseID=1)
        )

        with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
            assert ctrl.add_to_playlist("PL", 1, "n") is False
        assert "read only playlist" in caplog.text


# --- 3. WARNING 以上のログがログ枠へ転送される ---

class TestUILogHandler:
    def test_warning_record_forwarded_to_ui_queue(self):
        """インストールされたハンドラが WARNING レコードを _ui_queue へ積む"""
        app = _make_app()
        handler = app._create_ui_log_handler()
        handler.setLevel(logging.WARNING)
        test_logger = logging.getLogger("itt1_test_forward")
        test_logger.addHandler(handler)
        test_logger.propagate = False
        try:
            test_logger.warning("テスト警告メッセージ")
            kind, msg = app._ui_queue.get_nowait()
            assert kind == "log"
            assert "テスト警告メッセージ" in msg
        finally:
            test_logger.removeHandler(handler)

    def test_debug_record_not_forwarded(self):
        """DEBUG レベルはログ枠を埋めないよう転送しない"""
        app = _make_app()
        handler = app._create_ui_log_handler()
        handler.setLevel(logging.WARNING)
        test_logger = logging.getLogger("itt1_test_filter")
        test_logger.addHandler(handler)
        test_logger.propagate = False
        try:
            test_logger.debug("ノイズ")
            assert app._ui_queue.empty()
        finally:
            test_logger.removeHandler(handler)

    def test_handler_survives_closed_queue(self):
        """キューが無くても emit で例外を投げない（終了処理中の安全側）"""
        app = _make_app()
        app._ui_queue = None
        handler = app._create_ui_log_handler()
        record = logging.LogRecord(
            "t", logging.WARNING, __file__, 1, "msg", None, None
        )
        handler.emit(record)  # 例外を投げないこと
