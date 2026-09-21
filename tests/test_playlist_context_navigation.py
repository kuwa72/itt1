"""Issue #18: プレイリストコンテキストでの次/前曲遷移テスト。

プレイリスト内の曲を再生中に「次の曲」を押すと、iTunes の再生キューではなく
そのプレイリストの表示順（PlayOrderIndex/Item index）で隣接するトラックを
再生する。コンテキストが無い・現在トラックがプレイリスト内に見つからない
場合のみ NextTrack()/PreviousTrack() にフォールバックする。

COM オブジェクトは SimpleNamespace/FakeTrackCollection で模倣する
（既存テスト同様 __new__ + self.itunes 注入。実 iTunes は不要）。
"""

import queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gui_tk import ITunesTkApp
from music_controller_base import MacOSMusicController
from music_controller_windows import WindowsMusicController


class FakeTrackCollection:
    """IITTrackCollection 相当の最小フェイク（Count/Item/イテレーション）。"""

    def __init__(self, tracks):
        self._tracks = list(tracks)

    @property
    def Count(self):
        return len(self._tracks)

    def Item(self, i):
        if not 1 <= i <= len(self._tracks):
            raise IndexError(i)
        return self._tracks[i - 1]

    def __iter__(self):
        return iter(self._tracks)


def _track(dbid, name="t", poi=None):
    t = SimpleNamespace(TrackDatabaseID=dbid, Name=name)
    if poi is not None:
        t.PlayOrderIndex = poi
    t.Play = MagicMock(name=f"Play[{dbid}]")
    return t


def _playlist(name, tracks, search_result=None):
    pl = SimpleNamespace(Name=name, Tracks=FakeTrackCollection(tracks))
    pl.PlayFirstTrack = MagicMock(name="PlayFirstTrack")
    if search_result is not None:
        pl.Search = MagicMock(name="Search", return_value=search_result)
    return pl


def _ctrl(current_track=None, current_playlist=None):
    """__init__ を通さず itunes フェイクだけ注入したコントローラーを返す。"""
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    ctrl.itunes = SimpleNamespace(
        CurrentTrack=current_track,
        CurrentPlaylist=current_playlist,
        NextTrack=MagicMock(name="NextTrack"),
        PreviousTrack=MagicMock(name="PreviousTrack"),
    )
    return ctrl


# --- 次曲遷移: プレイリストコンテキスト ---


def test_next_track_plays_adjacent_track_in_playlist():
    """コンテキストのプレイリスト内で現在トラックの次のインデックスを Play() する"""
    tracks = [_track(i) for i in range(1, 6)]
    pl = _playlist("PL", tracks)
    ctrl = _ctrl(current_track=tracks[1], current_playlist=SimpleNamespace(Name="PL"))
    ctrl._find_playlist_by_name = MagicMock(return_value=pl)

    ctrl.play_next_track("PL")

    tracks[2].Play.assert_called_once()
    ctrl.itunes.NextTrack.assert_not_called()


def test_previous_track_plays_adjacent_track_in_playlist():
    tracks = [_track(i) for i in range(1, 6)]
    pl = _playlist("PL", tracks)
    ctrl = _ctrl(current_track=tracks[2], current_playlist=SimpleNamespace(Name="PL"))
    ctrl._find_playlist_by_name = MagicMock(return_value=pl)

    ctrl.play_previous_track("PL")

    tracks[1].Play.assert_called_once()
    ctrl.itunes.PreviousTrack.assert_not_called()


def test_next_track_uses_current_playlist_when_no_explicit_context():
    """引数なしでも itunes.CurrentPlaylist をコンテキストとして使う"""
    tracks = [_track(i) for i in range(1, 6)]
    pl = _playlist("PL", tracks)
    ctrl = _ctrl(current_track=tracks[0], current_playlist=SimpleNamespace(Name="PL"))
    ctrl._find_playlist_by_name = MagicMock(return_value=pl)

    ctrl.play_next_track()

    tracks[1].Play.assert_called_once()
    ctrl.itunes.NextTrack.assert_not_called()


def test_next_track_does_not_call_play_first_track():
    """隣接遷移では PlayFirstTrack() ハックを呼ばない（先頭曲の一瞬再生を防ぐ）"""
    tracks = [_track(i) for i in range(1, 6)]
    pl = _playlist("PL", tracks)
    ctrl = _ctrl(current_track=tracks[1], current_playlist=SimpleNamespace(Name="PL"))
    ctrl._find_playlist_by_name = MagicMock(return_value=pl)

    ctrl.play_next_track("PL")

    pl.PlayFirstTrack.assert_not_called()


def test_next_track_resolves_index_via_search_poi():
    """CurrentTrack に PlayOrderIndex が無い場合、Search + PlayOrderIndex で
    プレイリスト内インデックスを特定する"""
    tracks = [_track(i) for i in range(1, 6)]
    candidate = SimpleNamespace(TrackDatabaseID=3, PlayOrderIndex=3)
    pl = _playlist("PL", tracks, search_result=FakeTrackCollection([candidate]))
    # CurrentTrack はライブラリトラック相当（PlayOrderIndex 属性なし）
    current = SimpleNamespace(TrackDatabaseID=3, Name="t3")
    ctrl = _ctrl(current_track=current, current_playlist=SimpleNamespace(Name="PL"))
    ctrl._find_playlist_by_name = MagicMock(return_value=pl)

    ctrl.play_next_track("PL")

    tracks[3].Play.assert_called_once()
    ctrl.itunes.NextTrack.assert_not_called()


# --- フォールバック ---


def test_next_track_falls_back_without_context():
    """プレイリストコンテキストが無ければ従来通り NextTrack()"""
    ctrl = _ctrl(current_track=_track(1), current_playlist=None)
    ctrl._find_playlist_by_name = MagicMock(return_value=None)

    ctrl.play_next_track()

    ctrl.itunes.NextTrack.assert_called_once()


def test_previous_track_falls_back_without_context():
    ctrl = _ctrl(current_track=_track(1), current_playlist=None)
    ctrl._find_playlist_by_name = MagicMock(return_value=None)

    ctrl.play_previous_track()

    ctrl.itunes.PreviousTrack.assert_called_once()


def test_next_track_falls_back_when_current_track_not_in_playlist():
    """コンテキストのプレイリスト内に現在トラックが無ければフォールバック"""
    tracks = [_track(i) for i in range(1, 4)]
    pl = _playlist("PL", tracks)
    ctrl = _ctrl(current_track=_track(99), current_playlist=SimpleNamespace(Name="PL"))
    ctrl._find_playlist_by_name = MagicMock(return_value=pl)

    ctrl.play_next_track("PL")

    ctrl.itunes.NextTrack.assert_called_once()
    for t in tracks:
        t.Play.assert_not_called()


def test_next_track_falls_back_at_playlist_end():
    """末尾トラックでは隣接が無いのでフォールバック"""
    tracks = [_track(i) for i in range(1, 4)]
    pl = _playlist("PL", tracks)
    ctrl = _ctrl(current_track=tracks[2], current_playlist=SimpleNamespace(Name="PL"))
    ctrl._find_playlist_by_name = MagicMock(return_value=pl)

    ctrl.play_next_track("PL")

    ctrl.itunes.NextTrack.assert_called_once()


def test_previous_track_falls_back_at_playlist_start():
    tracks = [_track(i) for i in range(1, 4)]
    pl = _playlist("PL", tracks)
    ctrl = _ctrl(current_track=tracks[0], current_playlist=SimpleNamespace(Name="PL"))
    ctrl._find_playlist_by_name = MagicMock(return_value=pl)

    ctrl.play_previous_track("PL")

    ctrl.itunes.PreviousTrack.assert_called_once()


def test_next_track_noop_when_disconnected():
    ctrl = _ctrl()
    ctrl.itunes = None
    ctrl.play_next_track("PL")  # 例外にならないこと


# --- macOS 側 API 契約 ---


def test_macos_next_previous_accept_playlist_name_arg():
    """gui_tk は playlist_name を渡すため、macOS 側も同じ引数を受け取る"""
    ctrl = MacOSMusicController()
    with patch.object(ctrl, "_run_applescript") as run:
        ctrl.play_next_track("PL")
        ctrl.play_previous_track("PL")
    assert run.call_count == 2


# --- gui_tk 連携 ---


def _make_app() -> ITunesTkApp:
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.ctrl = MagicMock(name="ctrl")
    app._com_task_queue = queue.Queue()
    app._closing = False
    app._playback_playlist = None
    app.last_action = "-"
    return app


def test_next_track_submits_playlist_context_to_com_worker():
    """next_track は COM ワーカーへ play_next_track(playlist) を投入する
    （UIスレッドをブロックしない・再生コンテキストを引き渡す）"""
    app = _make_app()
    app._playback_playlist = "PL"

    app.next_track()

    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert fn == "play_next_track"
    assert args == ("PL",)


def test_prev_track_submits_playlist_context_to_com_worker():
    app = _make_app()
    app._playback_playlist = "PL"

    app.prev_track()

    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert fn == "play_previous_track"
    assert args == ("PL",)


def test_task_result_sets_playback_playlist_on_play_track_success():
    """play_track_by_ids が成功したら再生コンテキストのプレイリストを記録する"""
    app = _make_app()

    app._handle_task_result(("play_track", "name", "PL"), True)

    assert app._playback_playlist == "PL"


def test_task_result_clears_playback_playlist_for_contextless_play():
    """プレイリスト名を持たない再生（location 再生等）ではコンテキストをクリア"""
    app = _make_app()
    app._playback_playlist = "PL"

    app._handle_task_result(("play_track", "name"), True)

    assert app._playback_playlist is None


def test_task_result_keeps_context_on_play_track_failure():
    """再生失敗時はコンテキストを変えない"""
    app = _make_app()
    app._playback_playlist = "PL"

    app._handle_task_result(("play_track", "name", "OTHER"), False)

    assert app._playback_playlist == "PL"


def test_task_result_sets_playback_playlist_on_play_playlist_success():
    """プレイリスト再生（ダブルクリック）成功時もコンテキストを記録する"""
    app = _make_app()

    app._handle_task_result(("play_playlist", "PL"), True)

    assert app._playback_playlist == "PL"
