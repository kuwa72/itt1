"""Issue #35/#39: 次/前曲遷移がGUIトラック一覧の表示順に従うテスト。

コントローラー自体がプレイヤーであり、「次/前」はユーザーが見ている
トラック一覧の並び（XML play_order + ユーザーによる列ソート結果）に従う。
再生はローカル再生エンジン（playback_engine）へ行の loc: タグの
ファイルパスを渡して行う。iTunes 再生経路へのフォールバックは無い。
"""

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


def _loc(name):
    return f"file://localhost/C:/music/{name}.m4a"


def _make_app(current_dbid=200):
    app = ITunesTkApp.__new__(ITunesTkApp)
    app._closing = False
    app.last_action = ""
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app._ui_queue = None
    app.engine = MagicMock(name="engine")
    app._playback_playlist = "PL"
    app._track_loading_playlist = "PL"
    app._latest_track_info = {"dbid": current_dbid}
    app._current_track_meta = None
    app._track_tree_item_meta = {}
    rows = [
        {"dbid": 100, "pid": "P1", "src": 1, "pl": 2, "tid": 11, "po": 1,
         "loc": _loc("A"), "name": "A"},
        {"dbid": 200, "pid": "P2", "src": 1, "pl": 2, "tid": 22, "po": 2,
         "loc": _loc("B"), "name": "B"},
        {"dbid": 300, "pid": "P3", "src": 1, "pl": 2, "tid": 33, "po": 3,
         "loc": _loc("C"), "name": "C"},
    ]
    app.track_tree, app._iids = _make_track_tree(rows)
    app._rows = rows
    return app


class TestListOrderNavigation:
    def test_next_plays_next_row_in_displayed_list(self):
        """次: 現在曲の隣（表示上の次の行）をローカルエンジンで再生"""
        app = _make_app(current_dbid=200)

        app.next_track()

        app.engine.play.assert_called_once()
        assert "C.m4a" in app.engine.play.call_args[0][0]
        assert app._current_track_meta["dbid"] == 300
        assert "次の曲" in app.last_action

    def test_prev_plays_previous_row(self):
        app = _make_app(current_dbid=200)

        app.prev_track()

        app.engine.play.assert_called_once()
        assert "A.m4a" in app.engine.play.call_args[0][0]
        assert app._current_track_meta["dbid"] == 100
        assert "前の曲" in app.last_action

    def test_next_at_last_row_is_noop(self):
        """一覧末尾で次 → 何もしない"""
        app = _make_app(current_dbid=300)

        app.next_track()

        app.engine.play.assert_not_called()
        assert "次の曲がありません" in app.last_action

    def test_prev_at_first_row_is_noop(self):
        app = _make_app(current_dbid=100)

        app.prev_track()

        app.engine.play.assert_not_called()
        assert "前の曲がありません" in app.last_action

    def test_current_not_in_list_is_noop(self):
        """別プレイリスト表示中など、現在曲が一覧に無い場合は何もしない"""
        app = _make_app(current_dbid=999)

        app.next_track()

        app.engine.play.assert_not_called()

    def test_follows_visible_order_after_resort(self):
        """列ソートで表示順が変わった場合、その表示順に従う"""
        app = _make_app(current_dbid=200)
        # 表示順を逆順にする（iid の並びを反転）
        app.track_tree.get_children.return_value = ("I2", "I1", "I0")

        app.next_track()

        app.engine.play.assert_called_once()
        assert "A.m4a" in app.engine.play.call_args[0][0]  # 表示上 B の次は A

    def test_row_without_location_not_playable(self):
        """ファイルパスを持たない行（クラウド曲等）は再生不可"""
        app = _make_app(current_dbid=200)
        app._rows[2]["loc"] = None
        app.track_tree, app._iids = _make_track_tree(app._rows)

        app.next_track()

        app.engine.play.assert_not_called()
        assert any("再生不可" in m for m in app._action_log)

    def test_no_engine_not_playable(self):
        """エンジンが無い（mpv未導入等）場合はログして終わる"""
        app = _make_app(current_dbid=200)
        app.engine = None

        app.next_track()

        assert any("再生エンジンがありません" in m for m in app._action_log)

    def test_engine_play_failure_is_logged(self):
        """engine.play が失敗しても例外を投げずログに残す"""
        app = _make_app(current_dbid=200)
        app.engine.play.side_effect = RuntimeError("decode error")

        app.next_track()

        assert any("再生失敗" in m for m in app._action_log)


class TestTrackIdsFromItem:
    def test_parses_all_tags(self):
        app = _make_app()
        ids = app._track_ids_from_item("I1")
        assert ids == {
            "dbid": 200, "pid": "P2", "source_id": 1, "playlist_id": 2,
            "track_id": 22, "play_order": 2, "location": _loc("B"),
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
