"""Issue #39: 曲の自然終了（EOF）でトラック一覧の次の行を再生するテスト。

ローカル再生エンジンが end-file イベントを発行し、mpv イベントスレッドから
_ui_queue 経由で UI スレッドの _on_engine_eof へ届く。
EOF / 再生エラー時は終了した曲の一覧上の次の行を再生する。
iTunes のキューやポーリング補正（Issue #37 のロジック）は存在しない。
"""

import queue
from collections import deque
from unittest.mock import MagicMock

from gui_tk import ITunesTkApp
from tests.test_list_order_navigation import _loc, _make_track_tree


def _make_app(current_dbid=200):
    app = ITunesTkApp.__new__(ITunesTkApp)
    app._ui_queue = queue.Queue()
    app._closing = False
    app.last_action = ""
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app.engine = MagicMock(name="engine")
    app._now_playing_playlist = "PL"
    app._track_loading_playlist = "PL"
    app._current_track_meta = {"dbid": current_dbid, "name": "B"}
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
    return app


class TestNaturalTrackEnd:
    def test_eof_plays_next_row(self):
        """EOF: 終了した曲の一覧上の次の行をエンジンで再生"""
        app = _make_app(current_dbid=200)

        app._on_engine_eof("eof", None)

        app.engine.play.assert_called_once()
        assert "C.m4a" in app.engine.play.call_args[0][0]
        assert app._current_track_meta["dbid"] == 300

    def test_eof_at_last_row_does_nothing(self):
        """一覧末尾の曲の EOF → 何もしない（止まる）"""
        app = _make_app(current_dbid=300)

        app._on_engine_eof("eof", None)

        app.engine.play.assert_not_called()

    def test_eof_with_track_not_in_list_does_nothing(self):
        """終了した曲が一覧に無い（別プレイリスト表示中）は何もしない"""
        app = _make_app(current_dbid=999)

        app._on_engine_eof("eof", None)

        app.engine.play.assert_not_called()

    def test_eof_without_current_meta_does_nothing(self):
        """再生中メタ情報が無い状態での EOF は何もしない"""
        app = _make_app()
        app._current_track_meta = None

        app._on_engine_eof("eof", None)

        app.engine.play.assert_not_called()

    def test_error_event_logs_and_advances(self):
        """再生エラー: 理由をログへ出し、次の行へ進む"""
        app = _make_app(current_dbid=200)

        app._on_engine_eof("error", 1)

        assert any("再生エラー" in m for m in app._action_log)
        app.engine.play.assert_called_once()
        assert "C.m4a" in app.engine.play.call_args[0][0]

    def test_eof_while_closing_does_nothing(self):
        """終了処理中に届いた EOF は処理しない"""
        app = _make_app(current_dbid=200)
        app._closing = True

        app._on_engine_eof("eof", None)

        app.engine.play.assert_not_called()

    def test_stop_reason_does_nothing(self):
        """stop/quit 等の EOF 以外の終了理由は次行遷移しない"""
        app = _make_app(current_dbid=200)

        app._on_engine_eof("stop", None)
        app._on_engine_eof("quit", None)

        app.engine.play.assert_not_called()


class TestEofEventRouting:
    def test_eof_message_routed_via_ui_queue(self):
        """mpv イベントスレッドからの EOF が _ui_queue 経由で
        _on_engine_eof に届き次行を再生する（_drain_ui_queue の統合確認）"""
        app = _make_app(current_dbid=200)
        app._ui_queue.put(("engine_eof", "eof", None))

        app._drain_ui_queue()

        app.engine.play.assert_called_once()
        assert "C.m4a" in app.engine.play.call_args[0][0]
