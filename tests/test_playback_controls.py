"""再生コントロールUI（⏮/⏯/⏭ + 音量スライダー + シークバー）のテスト。

Issue #39: 再生操作は全てローカル再生エンジン（playback_engine）へ委譲される。
- 再生/一時停止 → engine.toggle_pause()
- ±N秒スキップ → engine.seek_rel()
- シークバー → engine.seek_abs()
- 音量スライダー → デバウンス後に engine.set_volume()
- エンジン状態の volume は _apply_track_info でスライダー/ラベルへ反映するが、
  ドラッグ中（_volume_seeking）はユーザー操作を優先して上書きしない

tkinter は conftest.py のフェイクモジュール経由。
ITunesTkApp は __init__ を通さず __new__ + 必要属性の代入で生成する。
"""

import queue
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock

from gui_tk import ITunesTkApp


def _make_app() -> ITunesTkApp:
    """エンジンをモック化したテスト用インスタンス。"""
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.root = MagicMock(name="root")
    app.ctrl = MagicMock(name="ctrl")
    app.engine = MagicMock(name="engine")
    app.config = MagicMock(name="config")
    app.config.config.refresh_interval = 0.5
    app.config.config.skip_seconds = 10
    app.track_status = MagicMock(name="track_status")
    app.track_title = MagicMock(name="track_title")
    app.track_artist = MagicMock(name="track_artist")
    app.track_album = MagicMock(name="track_album")
    app.track_time = MagicMock(name="track_time")
    app.progress_bar = MagicMock(name="progress_bar")
    app.volume_scale = MagicMock(name="volume_scale")
    app.volume_label = MagicMock(name="volume_label")
    app.last_action = "-"
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app._seeking = False
    app._synced_dbid = None
    app._synced_playlist = None
    app._closing = False
    app._ui_queue = queue.Queue()
    app._latest_track_info = None
    app._current_track_meta = None
    # シーク/音量デバウンス
    app._seek_pending_value = None
    app._seek_after_id = None
    app._volume_seeking = False
    app._volume_pending_value = None
    app._volume_after_id = None
    app._now_playing_playlist = None
    app._after = MagicMock(name="_after", side_effect=lambda ms, fn: "after-id")
    return app


# --- 再生/一時停止・スキップ: エンジン直接呼出し ---

def test_toggle_play_pause_calls_engine():
    app = _make_app()

    app.toggle_play_pause()

    app.engine.toggle_pause.assert_called_once_with()


def test_toggle_play_pause_noop_without_engine():
    """エンジンが無い環境でも例外を投げない"""
    app = _make_app()
    app.engine = None

    app.toggle_play_pause()


def test_skip_forward_calls_engine_seek_rel():
    app = _make_app()

    app.skip_forward()

    app.engine.seek_rel.assert_called_once_with(10)


def test_skip_backward_calls_engine_seek_rel_negative():
    app = _make_app()

    app.skip_backward()

    app.engine.seek_rel.assert_called_once_with(-10)


# --- シークバー: デバウンス → engine.seek_abs ---

def test_flush_pending_seek_calls_engine_seek_abs():
    """シークバー操作はデバウンス後に engine.seek_abs(秒) へ変換される"""
    app = _make_app()
    app._latest_track_info = {"duration": 200}
    app._seek_pending_value = 50.0  # 50%

    app._flush_pending_seek()

    app.engine.seek_abs.assert_called_once_with(100.0)


def test_flush_pending_seek_is_idempotent():
    """発火済みのデバウンスコールバックが残っていても二重シークしない"""
    app = _make_app()
    app._latest_track_info = {"duration": 200}
    app._seek_pending_value = 50.0

    app._flush_pending_seek()
    app._flush_pending_seek()  # pending値は消費済み → 何もしない

    app.engine.seek_abs.assert_called_once()


def test_flush_pending_seek_noop_without_duration():
    """duration 不明（未再生）では seek しない"""
    app = _make_app()
    app._latest_track_info = {"duration": 0}
    app._seek_pending_value = 50.0

    app._flush_pending_seek()

    app.engine.seek_abs.assert_not_called()


# --- 音量スライダー: デバウンス → engine.set_volume ---

def _fake_event(x):
    return SimpleNamespace(x=x)


def test_on_volume_change_debounces_to_engine():
    """ドラッグ中の連続イベントはデバウンスされ、set_volume は1回だけ"""
    app = _make_app()
    app._volume_seeking = True

    for v in (10, 20, 30, 40, 50):
        app.on_volume_change(v)

    # 先行スケジュールはキャンセルされ、最後の1発だけが残る
    assert app.root.after_cancel.call_count == 4
    assert app._after.call_args[0][0] == app.SEEK_DEBOUNCE_MS

    app._flush_pending_volume()
    app.engine.set_volume.assert_called_once_with(50)


def test_on_volume_change_ignores_when_not_seeking():
    """ドラッグ中でなければ何もしない（ポーリング反映 set() の誤発火防止）"""
    app = _make_app()
    app._volume_seeking = False

    app.on_volume_change(50)

    app._after.assert_not_called()
    app.engine.set_volume.assert_not_called()


def test_flush_pending_volume_is_idempotent():
    """発火済みのデバウンスコールバックが残っていても二重投入しない"""
    app = _make_app()
    app._volume_pending_value = 30

    app._flush_pending_volume()
    app._flush_pending_volume()  # pending値は消費済み → 何もしない

    app.engine.set_volume.assert_called_once_with(30)


def test_flush_pending_volume_rounds_and_clips():
    """スライダーの浮動小数値は int へ丸め、0-100 にクリップする"""
    app = _make_app()
    app._volume_pending_value = "62.4"  # ttk.Scale command は文字列で渡す

    app._flush_pending_volume()
    app.engine.set_volume.assert_called_once_with(62)

    app._volume_pending_value = 120.0
    app._flush_pending_volume()
    app.engine.set_volume.assert_called_with(100)


def test_volume_press_seeks_to_click_position():
    """トラフクリックは event.x/幅 の割合へジャンプする（シークバーと同じ）"""
    app = _make_app()
    app.volume_scale.winfo_width.return_value = 200

    result = app._on_volume_press(_fake_event(50))  # 25%位置

    assert result == "break"
    assert app._volume_seeking is True
    app.volume_scale.set.assert_called_once_with(25.0)
    assert app._volume_pending_value == 25.0


def test_volume_drag_follows_mouse_position():
    app = _make_app()
    app.volume_scale.winfo_width.return_value = 200
    app._volume_seeking = True

    result = app._on_volume_drag(_fake_event(150))  # 75%位置

    assert result == "break"
    app.volume_scale.set.assert_called_with(75.0)
    assert app._volume_pending_value == 75.0


def test_volume_release_clears_seeking_flag():
    """ボタン解放でドラッグ中フラグを落とす（ポーリング追従を再開させる）"""
    app = _make_app()
    app._volume_seeking = True

    app._on_volume_release(_fake_event(0))

    assert app._volume_seeking is False


# --- エンジン状態 → UI反映 ---

def test_poll_engine_builds_info_from_engine_and_meta():
    """_poll_engine はエンジン状態 + 再生中メタ情報から表示用 info を合成する"""
    app = _make_app()
    app._current_track_meta = {"dbid": 7, "name": "n", "artist": "a", "album": "al"}
    app.engine.get_state.return_value = {
        "position": 12.5, "duration": 180.0, "is_playing": True, "volume": 62.0,
    }

    app._poll_engine()

    info = app._latest_track_info
    assert info["dbid"] == 7
    assert info["position"] == 12.5
    assert info["duration"] == 180.0
    assert info["is_playing"] is True
    assert info["volume"] == 62.0


def test_poll_engine_tolerates_missing_engine():
    """エンジンが無い/失敗してもポーリング自体は継続"""
    app = _make_app()
    app.engine = None

    app._poll_engine()

    assert app._latest_track_info["is_playing"] is False
    assert app._latest_track_info["volume"] is None


def test_apply_track_info_applies_volume_to_widgets():
    """ポーリングの volume をスライダーとラベルへ反映する"""
    app = _make_app()
    app._apply_track_info({
        "is_playing": True, "name": "n", "artist": "a", "album": "al",
        "position": 10, "duration": 100, "volume": 62,
    })

    app.volume_scale.set.assert_called_once_with(62)
    app.volume_label.configure.assert_called_once_with(text="音量: 62")


def test_apply_track_info_does_not_override_volume_while_dragging():
    """ドラッグ中はポーリング値でスライダー/ラベルを上書きしない（_seeking と同じガード）"""
    app = _make_app()
    app._volume_seeking = True

    app._apply_track_info({
        "is_playing": True, "name": "n", "position": 10, "duration": 100,
        "volume": 62,
    })

    app.volume_scale.set.assert_not_called()
    app.volume_label.configure.assert_not_called()


def test_apply_track_info_handles_volume_only_payload():
    """停止中（トラック情報なし）でも volume だけのメッセージで音量表示は更新される"""
    app = _make_app()

    app._apply_track_info({"volume": 40})

    app.volume_scale.set.assert_called_once_with(40)
    app.volume_label.configure.assert_called_once_with(text="音量: 40")
