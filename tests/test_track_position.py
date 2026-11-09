"""Issue #50: 再生中トラックの一覧内位置（N/M）表示のテスト。

再生中 dbid が表示中 track_tree 内にあれば「N/M」を track_status に付加する。
一覧に無い・未再生の場合は付加しない。フィルタ適用中はフィルタ後の一覧での
位置になる（get_children は表示中の行のみ返すため）。
"""

import queue
from collections import deque
from unittest.mock import MagicMock

from gui_tk import ITunesTkApp
from tests.test_list_order_navigation import _loc, _make_track_tree


def _make_app():
    app = ITunesTkApp.__new__(ITunesTkApp)
    app._ui_queue = queue.Queue()
    app._closing = False
    app.last_action = ""
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app.engine = MagicMock(name="engine")
    app._now_playing_playlist = "PL"
    app._track_loading_playlist = "PL"
    app._track_tree_item_meta = {}
    app._seeking = False
    app._volume_seeking = False
    app._synced_dbid = None
    app._synced_playlist = None
    app.volume_scale = MagicMock()
    app.volume_label = MagicMock()
    app.track_status = MagicMock()
    app.track_title = MagicMock()
    app.track_artist = MagicMock()
    app.track_album = MagicMock()
    app.track_time = MagicMock()
    app.progress_bar = MagicMock()
    rows = [
        {"dbid": 100, "loc": _loc("A"), "name": "A"},
        {"dbid": 200, "loc": _loc("B"), "name": "B"},
        {"dbid": 300, "loc": _loc("C"), "name": "C"},
    ]
    app.track_tree, app._iids = _make_track_tree(rows)
    return app


def _info(dbid):
    return {
        "dbid": dbid, "name": "x", "artist": "a", "album": "b",
        "duration": 180, "position": 30, "is_playing": True,
        "playlist": "PL", "volume": 50,
    }


class TestTrackPosition:
    def test_position_shown_when_playing_track_in_list(self):
        """一覧内の再生中曲 → 位置が付く"""
        app = _make_app()

        app._apply_track_info(_info(200))

        app.track_status.configure.assert_called_with(text="▶ 再生中 (2/3)")

    def test_position_first_and_last(self):
        app = _make_app()

        app._apply_track_info(_info(100))
        app.track_status.configure.assert_called_with(text="▶ 再生中 (1/3)")

        app._apply_track_info(_info(300))
        app.track_status.configure.assert_called_with(text="▶ 再生中 (3/3)")

    def test_no_position_when_track_not_in_list(self):
        """一覧に無い曲の再生中は位置を出さない"""
        app = _make_app()

        app._apply_track_info(_info(999))

        app.track_status.configure.assert_called_with(text="▶ 再生中")

    def test_no_position_when_not_playing(self):
        app = _make_app()
        info = _info(200)
        info["is_playing"] = False

        app._apply_track_info(info)

        app.track_status.configure.assert_called_with(text="⏸ 一時停止")

    def test_no_position_without_dbid(self):
        app = _make_app()

        app._apply_track_info(_info(None))

        app.track_status.configure.assert_called_with(text="▶ 再生中")

    def test_position_follows_filtered_order(self):
        """フィルタ後の一覧（Aのみ表示）ではフィルタ後の位置"""
        app = _make_app()
        # フィルタで A(dbid=100) のみ表示中
        app.track_tree.get_children.return_value = ("I0",)

        app._apply_track_info(_info(100))

        app.track_status.configure.assert_called_with(text="▶ 再生中 (1/1)")

    def test_empty_tree_no_position(self):
        app = _make_app()
        app.track_tree.get_children.return_value = ()

        app._apply_track_info(_info(200))

        app.track_status.configure.assert_called_with(text="▶ 再生中")
