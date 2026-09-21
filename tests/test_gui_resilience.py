"""Issue #1: UI更新ループ / キーハンドラ / COM呼出しの例外耐性テスト。

tkinter は conftest.py のフェイクモジュール経由。
ITunesTkApp は __init__ を通さず __new__ + 必要属性の代入で生成し、
update_ui_loop / on_key を直接呼び出す。
"""

import logging
import queue
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import tkinter as tk
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
    # Issue #5: update_ui_loop はCOMを直接呼ばず _ui_queue を drain する
    app._ui_queue = queue.Queue()
    app._latest_track_info = None
    app.slot_bank = 0
    app.slot_keys = list("1234567890-=") + list("qwertyuiop[]\\") + list("asdfghjkl;'")
    app.bank_size = len(app.slot_keys)
    return app


def _key_event(keysym: str = "space", state: int = 0):
    return SimpleNamespace(keysym=keysym, state=state)


# --- update_ui_loop ---

def test_update_ui_loop_reschedules_on_success():
    app = _make_app()
    app.update_ui_loop()
    app.root.after.assert_called_once()
    interval, callback = app.root.after.call_args[0]
    assert interval == 500
    assert callback == app.update_ui_loop


def test_update_ui_loop_reschedules_after_controller_error():
    """ポーリング結果適用中の例外でもループが再スケジュールされる"""
    app = _make_app()
    app._apply_track_info = MagicMock(side_effect=RuntimeError("apply error"))
    app._ui_queue.put(("track_info", {"dbid": 1}))
    app.update_ui_loop()  # 例外が外へ漏れないこと
    app.root.after.assert_called_once()
    assert app.root.after.call_args[0][1] == app.update_ui_loop


def test_update_ui_loop_reschedules_after_widget_error():
    """途中の widget 更新で例外が発生しても再スケジュールされる"""
    app = _make_app()
    app._ui_queue.put(("track_info", {
        "is_playing": True, "name": "n", "artist": "a", "album": "al",
        "position": 10, "duration": 100, "dbid": 1, "playlist": "p",
    }))
    app._on_track_changed = MagicMock(side_effect=RuntimeError("widget error"))
    app.update_ui_loop()
    app.root.after.assert_called_once()


def test_update_ui_loop_backoff_on_consecutive_errors():
    """連続失敗時は再スケジュール間隔が伸び、上限を超えない"""
    app = _make_app()
    app.bpm_label.configure.side_effect = RuntimeError("UI error")

    app.update_ui_loop()
    first = app.root.after.call_args[0][0]
    app.update_ui_loop()
    second = app.root.after.call_args[0][0]
    assert second > first

    for _ in range(20):
        app.update_ui_loop()
    assert app.root.after.call_args[0][0] <= app.UPDATE_LOOP_MAX_INTERVAL_MS


def test_update_ui_loop_backoff_resets_after_success():
    app = _make_app()
    app.bpm_label.configure.side_effect = RuntimeError("UI error")
    app.update_ui_loop()
    app.update_ui_loop()
    backed_off = app.root.after.call_args[0][0]
    assert backed_off > 500

    app.bpm_label.configure.side_effect = None
    app.update_ui_loop()
    assert app.root.after.call_args[0][0] == 500


# --- on_key ---

def test_on_key_returns_break_on_success():
    app = _make_app()
    app.toggle_play_pause = MagicMock()
    assert app.on_key(_key_event("space")) == "break"
    app.toggle_play_pause.assert_called_once()


def test_on_key_swallows_handler_exception(caplog):
    """ホットキーハンドラの例外が外へ漏れず、last_action とログに記録される"""
    app = _make_app()
    app.toggle_play_pause = MagicMock(side_effect=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, logger="gui_tk"):
        app.on_key(_key_event("space"))  # 例外が伝播しないこと
    assert "boom" in str(app.last_action)
    assert any("boom" in r.message for r in caplog.records)


def test_on_key_swallows_focus_error():
    """focus_get 自体が例外を投げても外へ漏れない"""
    app = _make_app()
    app.root.focus_get.side_effect = RuntimeError("focus error")
    app.on_key(_key_event("space"))


def test_on_key_ignores_keys_when_entry_focused():
    """Entry フォーカス中はホットキーを無効化する（従来動作の維持）"""
    app = _make_app()
    app.root.focus_get.return_value = tk.Entry()
    app.toggle_play_pause = MagicMock()
    assert app.on_key(_key_event("space")) is None
    app.toggle_play_pause.assert_not_called()


# --- WindowsMusicController COM calls ---

@pytest.mark.parametrize("method_name,com_name", [
    ("play_pause", "PlayPause"),
    ("play_next_track", "NextTrack"),
    ("play_previous_track", "PreviousTrack"),
])
def test_com_method_calls_itunes(method_name, com_name):
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = MagicMock(name="itunes")
    getattr(ctrl, method_name)()
    getattr(ctrl.itunes, com_name).assert_called_once()


@pytest.mark.parametrize("method_name,com_name", [
    ("play_pause", "PlayPause"),
    ("play_next_track", "NextTrack"),
    ("play_previous_track", "PreviousTrack"),
])
def test_com_method_error_does_not_propagate(method_name, com_name, caplog):
    """一時的な com_error が呼出し側(Tkコールバック)に伝播しない"""
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = MagicMock(name="itunes")
    getattr(ctrl.itunes, com_name).side_effect = RuntimeError("com_error: RPC_E_CALL_REJECTED")
    with caplog.at_level(logging.ERROR, logger="music_controller_windows"):
        getattr(ctrl, method_name)()  # 例外が伝播しないこと
    assert any("RPC_E_CALL_REJECTED" in r.message for r in caplog.records)


@pytest.mark.parametrize("method_name", [
    "play_pause", "play_next_track", "play_previous_track",
])
def test_com_method_noop_when_disconnected(method_name):
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = None
    getattr(ctrl, method_name)()  # 例外にならないこと
