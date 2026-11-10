"""Issue #57: セッション復元（プレイリスト/曲/音量/再生位置）のテスト。"""

import queue
from collections import deque
from unittest.mock import MagicMock

from config import ConfigManager
from gui_tk import ITunesTkApp
from tests.test_list_order_navigation import _loc, _make_track_tree


def _make_app():
    app = ITunesTkApp.__new__(ITunesTkApp)
    app._closing = False
    app._modal_open = 0
    app._ui_queue = queue.Queue()
    app._action_log = deque(maxlen=50)
    app.last_action = ""
    app.action_log_listbox = None
    app.engine = MagicMock(name="engine")
    app.config = MagicMock(name="config")
    app._playlist_raw_names = ["PL-A", "PL-B"]
    app.playlist_listbox = MagicMock(name="listbox")
    app._track_loading_playlist = "PL-A"
    app._now_playing_playlist = "PL-A"
    app._current_track_meta = {"dbid": 200, "name": "B"}
    app._latest_track_info = {"dbid": 200, "position": 42.0, "volume": 62}
    app._track_tree_item_meta = {}
    app._track_loading_dbid = None
    app._pending_resume = None
    app._pending_autoplay = None
    app._session_restored = False
    app._track_filter_query = ""
    app._after = MagicMock(name="_after")
    app.load_tracks = MagicMock(name="load_tracks")
    rows = [{"dbid": 100, "loc": _loc("A"), "name": "A"},
            {"dbid": 200, "loc": _loc("B"), "name": "B"},
            {"dbid": 300, "loc": _loc("C"), "name": "C"}]
    app.track_tree, app._iids = _make_track_tree(rows)
    return app


def test_config_session_roundtrip(tmp_path):
    cm = ConfigManager(str(tmp_path / "c.json"))
    cm.set_last_session(playlist="PL", dbid=7, position=42.5, volume=62)
    s = ConfigManager(str(tmp_path / "c.json")).get_last_session()
    assert s == {"playlist": "PL", "dbid": 7, "position": 42.5, "volume": 62}


def test_on_closing_saves_session():
    app = _make_app()
    app.root = MagicMock(name="root")
    app.root.geometry.return_value = "1040x860+0+0"
    app._ui_log_handler = None
    app._com_task_queue = queue.Queue()
    app._com_worker_stop = MagicMock()
    app._com_worker_thread = None
    app._track_loading_thread = None
    app._track_loading_cancel_event = None
    app._pending_after_ids = set()
    app.ctrl = MagicMock(name="ctrl")
    app._ui_queue_worker = None

    app.on_closing()

    app.config.set_last_session.assert_called_once_with(
        playlist="PL-A", dbid=200, position=42.0, volume=62,
    )


def test_restore_selects_playlist_and_loads():
    app = _make_app()
    app.config.get_last_session.return_value = {
        "playlist": "PL-B", "dbid": 200, "position": 42.0, "volume": 62,
    }

    app._restore_last_session()

    app.playlist_listbox.selection_set.assert_called_once_with(1)
    assert app._pending_resume == {"dbid": 200, "position": 42.0}
    app.engine.set_volume.assert_called_once_with(62)
    app.load_tracks.assert_called_once_with("PL-B")


def test_restore_runs_once():
    app = _make_app()
    app._session_restored = True
    app._restore_last_session()
    app.load_tracks.assert_not_called()


def test_restore_missing_playlist_noop():
    app = _make_app()
    app.config.get_last_session.return_value = {
        "playlist": "GONE", "dbid": 200, "position": 0, "volume": -1,
    }
    app._restore_last_session()
    app.load_tracks.assert_not_called()
    assert app._pending_resume is None


def test_resume_plays_row_and_schedules_seek():
    app = _make_app()
    app._pending_resume = {"dbid": 200, "position": 42.0}

    app._display_tracks_batch_sync([], None)

    app.engine.play.assert_called_once()
    assert "B.m4a" in app.engine.play.call_args[0][0]
    app._after.assert_called_once_with(400, app._finish_resume_seek, 42.0)
    assert app._pending_resume is None


def test_finish_resume_seek_pauses_at_position():
    app = _make_app()
    app.engine.get_state.return_value = {"is_playing": True}

    app._finish_resume_seek(42.0)

    app.engine.seek_abs.assert_called_once_with(42.0)
    app.engine.toggle_pause.assert_called_once()


def test_finish_resume_seek_no_toggle_when_paused():
    app = _make_app()
    app.engine.get_state.return_value = {"is_playing": False}

    app._finish_resume_seek(10.0)

    app.engine.toggle_pause.assert_not_called()
