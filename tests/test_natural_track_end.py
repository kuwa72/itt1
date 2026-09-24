"""Issue #37: 曲の自然終了時にトラック一覧の次の行を再生するテスト。

コントローラー利用中は一覧の表示順が再生順の正。ポーリングで
「再生中かつ末尾2秒以内 → 曲変化/停止」を検知したら自然終了とみなし、
終了した曲の一覧上の次の行を再生する。手動遷移は期待値/抑制フラグで除外。
"""

import queue
from collections import deque
from unittest.mock import MagicMock

from gui_tk import ITunesTkApp
from tests.test_list_order_navigation import _make_track_tree


def _make_app():
    app = ITunesTkApp.__new__(ITunesTkApp)
    app._ui_queue = queue.Queue()
    app._com_task_queue = queue.Queue()
    app._closing = False
    app.last_action = ""
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app.ctrl = MagicMock(name="ctrl")
    app._playback_playlist = "PL"
    app._track_loading_playlist = "PL"
    app._track_tree_item_meta = {}
    app._expected_dbid = None
    app._suppress_track_correction = False
    rows = [
        {"dbid": 100, "pid": "P1", "src": 1, "pl": 2, "tid": 11, "po": 1, "name": "A"},
        {"dbid": 200, "pid": "P2", "src": 1, "pl": 2, "tid": 22, "po": 2, "name": "B"},
        {"dbid": 300, "pid": "P3", "src": 1, "pl": 2, "tid": 33, "po": 3, "name": "C"},
    ]
    app.track_tree, app._iids = _make_track_tree(rows)
    # 既存の同期処理に必要な属性
    app._synced_dbid = None
    app._synced_playlist = None
    app._seeking = False
    app._volume_seeking = False
    app.volume_scale = MagicMock()
    app.volume_label = MagicMock()
    app.track_status = MagicMock()
    app.track_title = MagicMock()
    app.track_artist = MagicMock()
    app.track_album = MagicMock()
    app.track_time = MagicMock()
    app.progress_bar = MagicMock()
    app._prev_track_info = None
    return app


def _info(dbid, playing=True, pos=0, dur=180):
    return {
        "dbid": dbid, "name": "x", "artist": "a", "album": "b",
        "duration": dur, "position": pos, "is_playing": playing,
        "playlist": "PL", "volume": 50,
    }


def _ended_prev(dbid):
    """直前ポーリング: 再生中・末尾2秒以内"""
    return _info(dbid, playing=True, pos=179, dur=180)


def _drain_task(app):
    try:
        return app._com_task_queue.get_nowait()
    except queue.Empty:
        return None


class TestNaturalTrackEnd:
    def test_wrong_next_track_is_corrected(self):
        """自然終了後に iTunes が一覧の次と違う曲に進んだ → 一覧の次の行を再生"""
        app = _make_app()
        app._prev_track_info = _ended_prev(200)

        app._apply_track_info(_info(500))  # iTunes がキューの別曲に進んだ

        task = _drain_task(app)
        assert task is not None
        fn, args, kwargs, tag = task
        assert fn == "play_track_by_ids"
        assert args[3] == 300  # 一覧上 B の次は C

    def test_correct_queue_advance_left_alone(self):
        """iTunes の遷移先が一覧の次と一致する場合は何もしない"""
        app = _make_app()
        app._prev_track_info = _ended_prev(200)

        app._apply_track_info(_info(300))  # iTunes が一覧の次 C に進んだ

        assert _drain_task(app) is None

    def test_stopped_at_end_plays_next_row(self):
        """キュー枯渇で再生が止まった → 一覧の次の行を再生開始"""
        app = _make_app()
        app._prev_track_info = _ended_prev(200)

        app._apply_track_info(_info(200, playing=False, pos=180, dur=180))

        task = _drain_task(app)
        assert task is not None
        fn, args, kwargs, tag = task
        assert fn == "play_track_by_ids"
        assert args[3] == 300

    def test_stopped_current_track_none_also_advances(self):
        """停止後に CurrentTrack が None になるケースでも次の行を再生"""
        app = _make_app()
        app._prev_track_info = _ended_prev(200)

        app._apply_track_info(_info(None, playing=False))

        task = _drain_task(app)
        assert task is not None
        assert task[1][3] == 300

    def test_last_row_end_does_nothing(self):
        """一覧末尾の曲が終了した場合は何もしない（一覧の終わり）"""
        app = _make_app()
        app._prev_track_info = _ended_prev(300)

        app._apply_track_info(_info(500))

        assert _drain_task(app) is None

    def test_mid_track_pause_not_treated_as_end(self):
        """末尾2秒より手前での停止（ユーザーが一時停止）は補正しない"""
        app = _make_app()
        app._prev_track_info = _info(200, playing=True, pos=50, dur=180)

        app._apply_track_info(_info(200, playing=False, pos=50))

        assert _drain_task(app) is None

    def test_mid_track_change_follows_itunes(self):
        """途中での曲変更（iTunes側の手動操作等）は補正しない"""
        app = _make_app()
        app._prev_track_info = _info(200, playing=True, pos=50, dur=180)

        app._apply_track_info(_info(500, pos=0))

        assert _drain_task(app) is None

    def test_expected_manual_nav_not_corrected(self):
        """手動遷移で期待した dbid への変化は補正しない"""
        app = _make_app()
        app._prev_track_info = _ended_prev(200)
        app._expected_dbid = 300

        app._apply_track_info(_info(300))

        assert _drain_task(app) is None
        assert app._expected_dbid is None

    def test_suppress_flag_consumed(self):
        """フォールバック遷移（結果不明）の dbid 変化は補正せずフラグを消費"""
        app = _make_app()
        app._prev_track_info = _ended_prev(200)
        app._suppress_track_correction = True

        app._apply_track_info(_info(500))

        assert _drain_task(app) is None
        assert app._suppress_track_correction is False

    def test_ended_track_not_in_list_does_nothing(self):
        """終了した曲が一覧に無い（別プレイリスト表示中）は何もしない"""
        app = _make_app()
        app._prev_track_info = _ended_prev(999)

        app._apply_track_info(_info(500))

        assert _drain_task(app) is None


class TestExpectedDbidBookkeeping:
    def test_list_nav_sets_expected_dbid(self):
        """一覧内の次/前遷移は期待 dbid を記録する"""
        app = _make_app()
        app._latest_track_info = {"dbid": 200}

        app.next_track()

        assert app._expected_dbid == 300

    def test_fallback_nav_sets_suppress_flag(self):
        """一覧で扱えないフォールバック遷移は抑制フラグを立てる"""
        app = _make_app()
        app._latest_track_info = {"dbid": 999}

        app.next_track()

        assert app._suppress_track_correction is True
        fn, args, kwargs, tag = app._com_task_queue.get_nowait()
        assert fn == "play_next_track"
