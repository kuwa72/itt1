"""Issue #6: 終了処理とモーダル中のグローバルホットキー抑制テスト。

tkinter は conftest.py のフェイクモジュール経由。
ITunesTkApp は __init__ を通さず __new__ + 必要属性の代入で生成する。
"""

import inspect
import queue
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pythoncom

import gui_tk
from gui_tk import ITunesTkApp
from music_controller_windows import WindowsMusicController


def _make_app() -> ITunesTkApp:
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.root = MagicMock(name="root")
    app.root.focus_get.return_value = None
    app.ctrl = MagicMock(name="ctrl")
    app.ctrl.get_current_track_info.return_value = {}
    app.config = MagicMock(name="config")
    app.config.config.refresh_interval = 0.5  # 500ms
    # update_ui_loop が触るウィジェット群
    app.track_status = MagicMock(name="track_status")
    app.track_title = MagicMock(name="track_title")
    app.track_artist = MagicMock(name="track_artist")
    app.track_album = MagicMock(name="track_album")
    app.track_time = MagicMock(name="track_time")
    app.progress_bar = MagicMock(name="progress_bar")
    app.bpm_label = MagicMock(name="bpm_label")
    app.bpm_value = None
    app.last_action = "-"
    app._seeking = False
    app._synced_dbid = None
    app._synced_playlist = None
    app._update_loop_failures = 0
    # トラック読み込みワーカー管理
    app._track_loading_thread = None
    app._track_loading_cancel_event = None
    app._track_loading_queue = None
    app._track_loading_playlist = None
    app._track_loading_dbid = None
    app._track_load_generation = 0
    app._track_tree_item_meta = {}
    app.track_tree = MagicMock(name="track_tree")
    app.track_tree.get_children.return_value = ()
    # after / プレイリストクリック遅延実行
    app._playlist_click_after_id = None
    app._pending_after_ids = set()
    # スロット関連（on_key 用）
    app.slot_bank = 0
    app.slot_keys = list("1234567890-=") + list("qwertyuiop[]\\") + list("asdfghjkl;'")
    app.bank_size = len(app.slot_keys)
    app.quick_slots = []
    # 終了処理中 / モーダル表示中フラグ
    app._closing = False
    app._modal_open = 0
    return app


def _key_event(keysym: str, state: int = 0):
    return SimpleNamespace(keysym=keysym, state=state)


# --- WM_DELETE_WINDOW / on_closing ---

def test_wm_delete_window_protocol_is_registered():
    """__init__ で WM_DELETE_WINDOW に on_closing が接続されている"""
    src = inspect.getsource(gui_tk.ITunesTkApp.__init__)
    assert "WM_DELETE_WINDOW" in src
    assert "on_closing" in src


def test_on_closing_sets_cancel_event_and_destroys_root():
    """on_closing はワーカーの cancel_event をセットして root.destroy を呼ぶ"""
    app = _make_app()
    ev = threading.Event()
    app._track_loading_cancel_event = ev

    app.on_closing()

    assert ev.is_set()
    app.root.destroy.assert_called_once()


def test_on_closing_cancels_pending_after_ids():
    """管理下の after ID が after_cancel でキャンセルされる"""
    app = _make_app()
    app._pending_after_ids = {"after#1", "after#2"}
    app._playlist_click_after_id = "after#click"

    app.on_closing()

    cancelled = {c.args[0] for c in app.root.after_cancel.call_args_list}
    assert {"after#1", "after#2", "after#click"} <= cancelled


def test_on_closing_is_idempotent():
    """2回呼ばれても destroy 等が二重実行されない"""
    app = _make_app()
    app.on_closing()
    app.on_closing()
    app.root.destroy.assert_called_once()


def test_on_closing_waits_briefly_for_worker():
    """生存中のワーカーに対して短い timeout 付きで join する（UIを長く止めない）"""
    app = _make_app()
    worker = MagicMock(name="worker")
    worker.is_alive.return_value = True
    app._track_loading_thread = worker

    app.on_closing()

    worker.join.assert_called_once()
    timeout = worker.join.call_args.kwargs.get(
        "timeout", worker.join.call_args.args[0] if worker.join.call_args.args else None
    )
    assert timeout is not None and timeout <= 1.0


def test_on_closing_releases_com_via_controller():
    """コントローラーが close を持つ場合は呼び出して COM を解放する"""
    app = _make_app()
    app.on_closing()
    app.ctrl.close.assert_called_once()


# --- 終了中フラグによる再スケジュール停止 ---

def test_update_ui_loop_does_not_reschedule_when_closing():
    """終了処理中は update_ui_loop が after を再スケジュールしない"""
    app = _make_app()
    app._closing = True
    app.update_ui_loop()
    app.root.after.assert_not_called()


def test_poll_track_queue_does_not_reschedule_when_closing():
    """終了処理中は _poll_track_queue が即終了し after を呼ばない"""
    app = _make_app()
    app._closing = True
    gen = 1
    app._track_load_generation = gen
    q = queue.Queue()
    q.put([{"name": "t", "play_order": 1}])
    app._track_loading_queue = q
    app._track_loading_cancel_event = threading.Event()
    worker = MagicMock(name="worker")
    worker.is_alive.return_value = True
    app._track_loading_thread = worker

    app._poll_track_queue(gen)

    app.root.after.assert_not_called()
    app.track_tree.insert.assert_not_called()




def test_m_key_routes_through_on_closing():
    """'m' キーは root.destroy を直接呼ばず on_closing 経路を通る"""
    app = _make_app()
    app.on_closing = MagicMock(name="on_closing")

    assert app.on_key(_key_event("m")) == "break"

    app.on_closing.assert_called_once()
    app.root.destroy.assert_not_called()


# --- モーダル中のグローバルホットキー抑制 ---

def test_on_key_suppressed_while_modal_open():
    """モーダル表示中はスロット追加・プレイリスト作成等のホットキーが発火しない"""
    app = _make_app()
    app._modal_open = 1
    app.add_to_slot = MagicMock(name="add_to_slot")
    app.create_single_playlist = MagicMock(name="create_single_playlist")
    app.pick_slots = MagicMock(name="pick_slots")

    assert app.on_key(_key_event("1")) is None   # スロット追加
    assert app.on_key(_key_event("c")) is None   # プレイリスト作成ダイアログ
    assert app.on_key(_key_event("b")) is None   # スロット割当ダイアログ

    app.add_to_slot.assert_not_called()
    app.create_single_playlist.assert_not_called()
    app.pick_slots.assert_not_called()


def test_on_key_suppressed_while_closing():
    """終了処理中もグローバルホットキーを抑制する"""
    app = _make_app()
    app._closing = True
    app.toggle_play_pause = MagicMock(name="toggle_play_pause")
    assert app.on_key(_key_event("space")) is None
    app.toggle_play_pause.assert_not_called()


def test_pick_slots_holds_modal_flag_during_wait_window():
    """PlaylistPicker の wait_window 中に _modal_open が立っている"""
    app = _make_app()
    # Issue #5: pick_slots は get_all_playlists ではなく _playlist_raw_names キャッシュを使う
    app._playlist_raw_names = ["PL-A", "PL-B"]
    app._get_playlist_folder_map = lambda: {}
    app.quick_slots = []

    flags_during_wait = []

    def fake_wait_window(_dlg):
        flags_during_wait.append(app._modal_open)

    app.root.wait_window.side_effect = fake_wait_window
    fake_dlg = MagicMock(name="dlg")
    fake_dlg.result = None

    with patch("gui_tk.PlaylistPicker", return_value=fake_dlg):
        app.pick_slots()

    assert flags_during_wait, "wait_window が呼ばれていない"
    assert all(f > 0 for f in flags_during_wait)
    # ダイアログを抜けた後はフラグが解除されている
    assert app._modal_open == 0


def test_modal_flag_released_on_dialog_exception():
    """ダイアログが例外で閉じた場合でも _modal_open が解除される"""
    app = _make_app()
    app._playlist_raw_names = ["PL-A"]
    app._get_playlist_folder_map = lambda: {}
    app.quick_slots = []
    app.root.wait_window.side_effect = RuntimeError("dialog crashed")

    with patch("gui_tk.PlaylistPicker", return_value=MagicMock()):
        try:
            app.pick_slots()
        except RuntimeError:
            pass

    assert app._modal_open == 0


# --- WindowsMusicController.close ---

def test_controller_close_uninitializes_com():
    """close() で CoUninitialize が呼ばれ、itunes 参照が切れる"""
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = MagicMock(name="itunes")
    pythoncom.CoUninitialize.reset_mock()

    ctrl.close()

    pythoncom.CoUninitialize.assert_called_once()
    assert ctrl.itunes is None


def test_controller_close_tolerates_com_error():
    """CoUninitialize が例外を投げても close() は伝播させない"""
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = MagicMock(name="itunes")
    pythoncom.CoUninitialize.side_effect = RuntimeError("com error")
    try:
        ctrl.close()
    finally:
        pythoncom.CoUninitialize.side_effect = None
