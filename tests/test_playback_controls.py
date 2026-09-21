"""Issue #25: 再生コントロールUI（⏮/⏯/⏭ + 音量スライダー）と音量API契約のテスト。

- Windows/macOS 両コントローラーが get_volume()/set_volume(level) を公開する
  - Windows: itunes.SoundVolume の get/set（0-100 int、例外は None/False）
  - macOS:   AppleScript `sound volume` の get/set
- GUI の再生コントロールはUIスレッドでCOMを呼ばず _com_submit 経由
- 音量スライダーはシークバーと同じデバウンスパターンで set_volume を投入
- ポーリング結果の volume は _apply_track_info でスライダー/ラベルへ反映するが、
  ドラッグ中（_volume_seeking）はユーザー操作を優先して上書きしない

tkinter は conftest.py のフェイクモジュール経由。
ITunesTkApp は __init__ を通さず __new__ + 必要属性の代入で生成する。
"""

import queue
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gui_tk import ITunesTkApp
from music_controller_base import MacOSMusicController
from music_controller_windows import WindowsMusicController


# --- コントローラーAPI契約 ---

def _make_windows_ctrl() -> WindowsMusicController:
    """__init__（COM接続）を通さず self.itunes だけ注入したインスタンスを返す。"""
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = MagicMock(name="itunes")
    return ctrl


@pytest.mark.parametrize("cls", [WindowsMusicController, MacOSMusicController])
@pytest.mark.parametrize("method", ["get_volume", "set_volume"])
def test_controller_exposes_volume_api(cls, method):
    assert callable(getattr(cls, method, None)), (
        f"{cls.__name__}.{method} が存在しない（gui_tk が依存）"
    )


class TestWindowsVolume:
    def test_get_volume_reads_sound_volume(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes.SoundVolume = 62
        assert ctrl.get_volume() == 62

    def test_get_volume_returns_none_when_disconnected(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        assert ctrl.get_volume() is None

    def test_get_volume_returns_none_on_com_error(self):
        ctrl = _make_windows_ctrl()
        type(ctrl.itunes).SoundVolume = property(
            fget=MagicMock(side_effect=RuntimeError("com_error")),
            fset=MagicMock(),
        )
        assert ctrl.get_volume() is None

    def test_set_volume_writes_sound_volume(self):
        ctrl = _make_windows_ctrl()
        assert ctrl.set_volume(40) is True
        assert ctrl.itunes.SoundVolume == 40

    def test_set_volume_clips_to_range(self):
        ctrl = _make_windows_ctrl()
        ctrl.set_volume(150)
        assert ctrl.itunes.SoundVolume == 100
        ctrl.set_volume(-5)
        assert ctrl.itunes.SoundVolume == 0

    def test_set_volume_returns_false_when_disconnected(self):
        ctrl = _make_windows_ctrl()
        ctrl.itunes = None
        assert ctrl.set_volume(50) is False

    def test_set_volume_returns_false_on_com_error(self):
        ctrl = _make_windows_ctrl()
        type(ctrl.itunes).SoundVolume = property(
            fget=MagicMock(return_value=0),
            fset=MagicMock(side_effect=RuntimeError("com_error")),
        )
        assert ctrl.set_volume(50) is False


class TestMacOSVolume:
    def test_get_volume_runs_sound_volume_script(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="62") as run:
            assert ctrl.get_volume() == 62
        assert "sound volume" in run.call_args[0][0]

    def test_get_volume_returns_none_on_error(self):
        ctrl = MacOSMusicController()
        with patch.object(
            ctrl, "_run_applescript", side_effect=RuntimeError("osascript failed")
        ):
            assert ctrl.get_volume() is None

    def test_get_volume_returns_none_on_bad_output(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="not-a-number"):
            assert ctrl.get_volume() is None

    def test_set_volume_runs_set_script(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="") as run:
            assert ctrl.set_volume(40) is True
        script = run.call_args[0][0]
        assert "set sound volume to 40" in script

    def test_set_volume_clips_to_range(self):
        ctrl = MacOSMusicController()
        with patch.object(ctrl, "_run_applescript", return_value="") as run:
            ctrl.set_volume(150)
        assert "set sound volume to 100" in run.call_args[0][0]

    def test_set_volume_returns_false_on_error(self):
        ctrl = MacOSMusicController()
        with patch.object(
            ctrl, "_run_applescript", side_effect=RuntimeError("osascript failed")
        ):
            assert ctrl.set_volume(40) is False


# --- GUI ---

def _make_app() -> ITunesTkApp:
    """COMワーカースレッドは起動しないテスト用インスタンス。
    キューと音量スライダー関連ウィジェットだけ用意する。
    """
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.root = MagicMock(name="root")
    app.ctrl = MagicMock(name="ctrl")
    app.ctrl.get_current_track_info.return_value = {}
    app.config = MagicMock(name="config")
    app.config.config.refresh_interval = 0.5
    app.track_status = MagicMock(name="track_status")
    app.track_title = MagicMock(name="track_title")
    app.track_artist = MagicMock(name="track_artist")
    app.track_album = MagicMock(name="track_album")
    app.track_time = MagicMock(name="track_time")
    app.progress_bar = MagicMock(name="progress_bar")
    app.volume_scale = MagicMock(name="volume_scale")
    app.volume_label = MagicMock(name="volume_label")
    app.last_action = "-"
    app._seeking = False
    app._synced_dbid = None
    app._synced_playlist = None
    app._closing = False
    app._pending_after_ids = set()
    app._ui_queue = queue.Queue()
    app._com_task_queue = queue.Queue()
    app._com_worker_stop = threading.Event()
    app._latest_track_info = None
    # シーク/音量デバウンス
    app._seek_pending_value = None
    app._seek_after_id = None
    app._volume_seeking = False
    app._volume_pending_value = None
    app._volume_after_id = None
    app._playback_playlist = None
    return app


# --- 再生ボタン: COMワーカー経由 ---

def test_toggle_play_pause_submits_to_com_worker():
    """再生/一時停止ボタンはUIスレッドでCOMを呼ばず play_pause タスクを投入する"""
    app = _make_app()

    app.toggle_play_pause()

    app.ctrl.play_pause.assert_not_called()
    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert fn == "play_pause"


def test_next_track_button_submits_with_playlist_context():
    app = _make_app()
    app._playback_playlist = "PL"

    app.next_track()

    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert (fn, args) == ("play_next_track", ("PL",))


def test_prev_track_button_submits_with_playlist_context():
    app = _make_app()
    app._playback_playlist = "PL"

    app.prev_track()

    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert (fn, args) == ("play_previous_track", ("PL",))


# --- 音量スライダー: デバウンス ---

def _fake_event(x):
    return SimpleNamespace(x=x)


def test_on_volume_change_debounces_and_offloads_set_volume():
    """ドラッグ中の連続イベントはデバウンスされ、set_volume はワーカー経由で1回だけ"""
    app = _make_app()
    app._volume_seeking = True

    for v in (10, 20, 30, 40, 50):
        app.on_volume_change(v)

    # UIスレッドではCOMを呼ばない
    app.ctrl.set_volume.assert_not_called()
    # 先行スケジュールはキャンセルされ、最後の1発だけが残る
    assert app.root.after_cancel.call_count == 4
    assert app.root.after.call_args[0][0] == app.SEEK_DEBOUNCE_MS

    app._flush_pending_volume()
    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert (fn, args) == ("set_volume", (50,))


def test_on_volume_change_ignores_when_not_seeking():
    """ドラッグ中でなければ何もしない（ポーリング反映 set() の誤発火防止）"""
    app = _make_app()
    app._volume_seeking = False

    app.on_volume_change(50)

    app.root.after.assert_not_called()
    app.ctrl.set_volume.assert_not_called()


def test_flush_pending_volume_is_idempotent():
    """発火済みのデバウンスコールバックが残っていても二重投入しない"""
    app = _make_app()
    app._volume_pending_value = 30

    app._flush_pending_volume()
    app._flush_pending_volume()  # pending値は消費済み → 何もしない

    assert app._com_task_queue.qsize() == 1


def test_flush_pending_volume_rounds_and_clips():
    """スライダーの浮動小数値は int へ丸め、0-100 にクリップする"""
    app = _make_app()
    app._volume_pending_value = "62.4"  # ttk.Scale command は文字列で渡す

    app._flush_pending_volume()
    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert (fn, args) == ("set_volume", (62,))

    app._volume_pending_value = 120.0
    app._flush_pending_volume()
    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert (fn, args) == ("set_volume", (100,))


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


# --- ポーリング → UI反映 ---

def test_poll_track_info_includes_volume():
    """COMワーカーのポーリングは get_volume も取得して同じメッセージに同梱する"""
    app = _make_app()
    app.ctrl.get_current_track_info.return_value = {"dbid": 7}
    app.ctrl.get_volume.return_value = 62

    app._poll_track_info(app.ctrl)

    assert app._ui_queue.get_nowait() == ("track_info", {"dbid": 7, "volume": 62})


def test_poll_track_info_tolerates_missing_volume_api():
    """get_volume を持たない/失敗するコントローラーでもポーリング自体は継続"""
    app = _make_app()
    app.ctrl.get_current_track_info.return_value = {"dbid": 7}
    app.ctrl.get_volume.side_effect = RuntimeError("no volume")

    app._poll_track_info(app.ctrl)

    kind, info = app._ui_queue.get_nowait()
    assert kind == "track_info"
    assert info["dbid"] == 7
    assert info["volume"] is None


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
