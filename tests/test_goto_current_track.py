"""Issue #44: goto_current_track（「再生中へ」/ Ctrl+G）のローカル再生モデル追従テスト。

再生中トラックの所属プレイリストは、_play_tree_item 実行時点の表示プレイリスト名を
_now_playing_playlist へ記録して追跡する。_track_loading_playlist は「直近に
読み込み要求したプレイリスト」であり、再生後の表示切替で変わりうるため参照しない。

「再生中へ」は _on_track_changed と同じ経路で再生中プレイリストへ遷移し、
再生中行をハイライトする。再生中でなければ従来のメッセージを出す。
"""

import queue
from collections import deque
from unittest.mock import MagicMock

from gui_tk import ITunesTkApp
from tests.test_list_order_navigation import _loc, _make_track_tree


def _make_app(playing_playlist="PL-A", view_playlist="PL-B", dbid=200):
    """再生中プレイリスト=playing_playlist、表示中=view_playlist の状態を作る"""
    app = ITunesTkApp.__new__(ITunesTkApp)
    app._closing = False
    app.last_action = ""
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app._ui_queue = queue.Queue()
    app.engine = MagicMock(name="engine")
    app.engine.get_state.return_value = {"is_playing": True}
    app._manual_playlist_view = True
    app._now_playing_playlist = playing_playlist
    app._track_loading_playlist = view_playlist
    app._track_loading_dbid = None
    app._current_track_meta = {"dbid": dbid, "name": "B"}
    app._latest_track_info = {"dbid": dbid, "playlist": playing_playlist}
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
    app._select_playlist_in_listbox = MagicMock(name="_select_playlist_in_listbox")
    app.load_tracks = MagicMock(name="load_tracks")
    app._highlight_track_by_dbid = MagicMock(name="_highlight_track_by_dbid")
    return app


class TestNowPlayingPlaylistTracking:
    def test_play_tree_item_records_displayed_playlist(self):
        """再生開始時点の表示プレイリスト名を _now_playing_playlist へ記録する"""
        app = _make_app()
        app._now_playing_playlist = "STALE"  # 再生開始で上書きされることを確認する
        app._track_loading_playlist = "PL-A"

        app._play_tree_item(app._iids[0])

        assert app._now_playing_playlist == "PL-A"

    def test_now_playing_playlist_does_not_follow_later_view_switch(self):
        """再生後に別プレイリストを表示しても _now_playing_playlist は追従しない"""
        app = _make_app()
        app._now_playing_playlist = "STALE"
        app._track_loading_playlist = "PL-A"
        app._play_tree_item(app._iids[0])

        # load_tracks による表示切替を模倣（再生は継続中）
        app._track_loading_playlist = "PL-B"

        assert app._now_playing_playlist == "PL-A"

    def test_play_in_other_playlist_updates_tracking(self):
        """一覧内遷移などで別プレイリストの行を再生したら追跡先も更新される"""
        app = _make_app()
        app._now_playing_playlist = "STALE"
        app._track_loading_playlist = "PL-B"

        app._play_tree_item(app._iids[1])

        assert app._now_playing_playlist == "PL-B"

    def test_poll_engine_uses_now_playing_playlist(self):
        """_poll_engine が合成する info の playlist は _now_playing_playlist の値"""
        app = _make_app(playing_playlist="PL-A", view_playlist="PL-B")
        app._apply_track_info = MagicMock(name="_apply_track_info")

        app._poll_engine()

        assert app._latest_track_info["playlist"] == "PL-A"


class TestGotoCurrentTrack:
    def test_goto_navigates_to_playing_playlist_while_viewing_other(self):
        """表示中プレイリストと異なる再生中プレイリストへ遷移する"""
        app = _make_app(playing_playlist="PL-A", view_playlist="PL-B", dbid=200)

        app.goto_current_track()

        app._select_playlist_in_listbox.assert_called_once_with("PL-A")
        app.load_tracks.assert_called_once_with("PL-A")
        # 読み込み完了後のハイライト用に dbid が保存される（_on_track_changed 経路）
        assert app._track_loading_dbid == 200
        assert app._manual_playlist_view is False

    def test_goto_highlights_row_when_already_viewing_playing_playlist(self):
        """再生中プレイリストを表示済みなら load_tracks せず行ハイライトのみ"""
        app = _make_app(playing_playlist="PL-A", view_playlist="PL-A", dbid=200)
        app._manual_playlist_view = True

        app.goto_current_track()

        app.load_tracks.assert_not_called()
        app._highlight_track_by_dbid.assert_called_once_with(200)

    def test_goto_uses_now_playing_playlist_when_info_lacks_playlist(self):
        """ポーリング情報に playlist が無い場合は _now_playing_playlist を使う"""
        app = _make_app(playing_playlist="PL-A", view_playlist="PL-B", dbid=200)
        app._latest_track_info = {"dbid": 200}

        app.goto_current_track()

        app.load_tracks.assert_called_once_with("PL-A")

    def test_goto_without_playing_track_keeps_message(self):
        """再生中でなければ従来のメッセージを出して何もしない"""
        app = _make_app(playing_playlist=None, view_playlist="PL-B", dbid=None)
        app._latest_track_info = {}

        app.goto_current_track()

        assert "再生中のプレイリストがありません" in app.last_action
        app._select_playlist_in_listbox.assert_not_called()
        app.load_tracks.assert_not_called()

    def test_goto_clears_manual_view_flag(self):
        """手動表示中でも「再生中へ」は自動追従抑止を解除して遷移する"""
        app = _make_app(playing_playlist="PL-A", view_playlist="PL-B", dbid=200)
        app._manual_playlist_view = True

        app.goto_current_track()

        assert app._manual_playlist_view is False
