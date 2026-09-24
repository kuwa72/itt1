"""Issue #35: 次/前曲遷移がGUIトラック一覧の表示順に従うテスト。

コントローラー利用中は iTunes Controller 自体がプレイヤーであり、
「次/前」はユーザーが見ているトラック一覧の並び（XML play_order +
ユーザーによる列ソート結果）に従う。一覧内で解決できない場合のみ
iTunes 側のプレイリスト順/キューにフォールバックする。
"""

import queue
from collections import deque
from unittest.mock import MagicMock

from gui_tk import ITunesTkApp


def _make_track_tree(rows):
    """rows: [{'dbid':..,'pid':..,'src':..,'pl':..,'tid':..,'po':..,'loc':..,'name':..}, ...]
    → 表示順のまま children を返す track_tree モック"""
    tree = MagicMock(name="track_tree")
    iids = [f"I{i}" for i in range(len(rows))]
    tagmap = {}
    for iid, r in zip(iids, rows):
        tags = []
        for k in ("dbid", "pid", "src", "pl", "tid", "po", "loc"):
            v = r.get(k)
            if v is not None:
                tags.append(f"{k}:{v}")
        tagmap[iid] = tuple(tags)

    def _item(iid, opt=None):
        if opt == "tags":
            return tagmap[iid]
        return rows[iids.index(iid)].get("name", "")

    tree.get_children.return_value = tuple(iids)
    tree.item.side_effect = _item
    return tree, iids


def _make_app(current_dbid=200):
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
    app._latest_track_info = {"dbid": current_dbid}
    app._track_tree_item_meta = {}
    rows = [
        {"dbid": 100, "pid": "P1", "src": 1, "pl": 2, "tid": 11, "po": 1, "name": "A"},
        {"dbid": 200, "pid": "P2", "src": 1, "pl": 2, "tid": 22, "po": 2, "name": "B"},
        {"dbid": 300, "pid": "P3", "src": 1, "pl": 2, "tid": 33, "po": 3, "name": "C"},
    ]
    app.track_tree, app._iids = _make_track_tree(rows)
    app._rows = rows
    return app


class TestListOrderNavigation:
    def test_next_plays_next_row_in_displayed_list(self):
        """次: 現在曲の隣（表示上の次の行）を play_track_by_ids で再生"""
        app = _make_app(current_dbid=200)

        app.next_track()

        fn, args, kwargs, tag = app._com_task_queue.get_nowait()
        assert fn == "play_track_by_ids"
        assert args[3] == 300  # database_id は次の行のもの
        assert tag == ("play_track", "C", "PL")
        assert "次の曲" in app.last_action

    def test_prev_plays_previous_row(self):
        app = _make_app(current_dbid=200)

        app.prev_track()

        fn, args, kwargs, tag = app._com_task_queue.get_nowait()
        assert fn == "play_track_by_ids"
        assert args[3] == 100
        assert tag == ("play_track", "A", "PL")

    def test_next_at_last_row_falls_back(self):
        """一覧末尾で次 → iTunes 側の既存経路へフォールバック"""
        app = _make_app(current_dbid=300)

        app.next_track()

        fn, args, kwargs, tag = app._com_task_queue.get_nowait()
        assert fn == "play_next_track"
        assert args == ("PL",)

    def test_prev_at_first_row_falls_back(self):
        app = _make_app(current_dbid=100)

        app.prev_track()

        fn, args, kwargs, tag = app._com_task_queue.get_nowait()
        assert fn == "play_previous_track"
        assert args == ("PL",)

    def test_current_not_in_list_falls_back(self):
        """別プレイリスト表示中など、現在曲が一覧に無い場合はフォールバック"""
        app = _make_app(current_dbid=999)

        app.next_track()

        fn, args, kwargs, tag = app._com_task_queue.get_nowait()
        assert fn == "play_next_track"

    def test_follows_visible_order_after_resort(self):
        """列ソートで表示順が変わった場合、その表示順に従う"""
        app = _make_app(current_dbid=200)
        # 表示順を逆順にする（iid の並びを反転）
        app.track_tree.get_children.return_value = ("I2", "I1", "I0")

        app.next_track()

        fn, args, kwargs, tag = app._com_task_queue.get_nowait()
        assert fn == "play_track_by_ids"
        assert args[3] == 100  # 表示上、B(dbid=200)の次は A

    def test_no_worker_queue_falls_back_to_ctrl(self):
        """ワーカー無し時は従来通り ctrl.play_next_track へ"""
        app = _make_app(current_dbid=200)
        app._com_task_queue = None

        app.next_track()

        app.ctrl.play_next_track.assert_called_once_with("PL")

    def test_playlist_name_not_passed_for_lightweight_play(self):
        """一覧内遷移では iTunes 側コンテキスト確立（PlayFirstTrack 経路）を
        行わないため play_track_by_ids の playlist_name/play_order は None"""
        app = _make_app(current_dbid=200)

        app.next_track()

        fn, args, kwargs, tag = app._com_task_queue.get_nowait()
        assert args[6] is None  # playlist_name
        assert args[7] is None  # play_order


class TestTrackIdsFromItem:
    def test_parses_all_tags(self):
        app = _make_app()
        ids = app._track_ids_from_item("I1")
        assert ids == {
            "dbid": 200, "pid": "P2", "source_id": 1, "playlist_id": 2,
            "track_id": 22, "play_order": 2, "location": None,
        }

    def test_missing_tags_become_none(self):
        tree, iids = _make_track_tree([{"dbid": 5, "name": "X"}])
        app = _make_app()
        app.track_tree = tree
        ids = app._track_ids_from_item(iids[0])
        assert ids["dbid"] == 5
        assert ids["pid"] is None
        assert ids["location"] is None

    def test_bad_item_returns_none_fields(self):
        app = _make_app()
        app.track_tree.item.side_effect = Exception("gone")
        ids = app._track_ids_from_item("missing")
        assert ids["dbid"] is None
