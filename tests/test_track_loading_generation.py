"""Issue #2: トラック読み込みワーカーの世代管理テスト。

プレイリスト高速切替で旧世代ワーカーのバッチが新世代キューへ混入しないこと、
旧世代のポーラーが自然終了してポーラーが世代ごとに1系統であること、
UIスレッドが旧ワーカーを join でブロックしないことを検証する。
"""

import queue
import threading
import time
from unittest.mock import MagicMock

from gui_tk import ITunesTkApp


def _make_app() -> ITunesTkApp:
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.root = MagicMock(name="root")
    app.ctrl = MagicMock(name="ctrl")
    app.ctrl.get_current_track_info.return_value = {}
    app.track_tree = MagicMock(name="track_tree")
    app.track_tree.get_children.return_value = ()
    app.last_action = "-"
    # トラック読み込みワーカー管理
    app._track_loading_thread = None
    app._track_loading_cancel_event = None
    app._track_loading_queue = None
    app._track_loading_playlist = None
    app._track_loading_dbid = None
    app._track_load_generation = 0
    app._track_tree_item_meta = {}
    # ライブラリXMLまわり（load_tracks の早期リターンを通過させる）
    app._library_xml_exists = True
    app._library_xml_lock = threading.Lock()
    app._library_xml_playlists = []
    app._load_library_xml_if_needed = lambda: True
    return app


def _track(name: str, play_order: int = 1) -> dict:
    return {
        "name": name,
        "artist": "a",
        "album": "al",
        "duration": 0,
        "play_order": play_order,
        "dbid": play_order,
        "persistent_id": None,
        "source_id": None,
        "playlist_id": None,
        "track_id": play_order,
        "location": "",
        "date_added": None,
        "purchase_date": None,
    }


def _inserted_names(app) -> list:
    """track_tree.insert に渡された text（= トラック名）の一覧"""
    return [c.kwargs["text"] for c in app.track_tree.insert.call_args_list]


def _wait_for(mapping: dict, key, timeout: float = 2.0):
    """ワーカースレッドが on_batch を登録するまで待つ"""
    deadline = time.monotonic() + timeout
    while key not in mapping:
        if time.monotonic() > deadline:
            raise TimeoutError(f"worker for {key!r} did not start")
        time.sleep(0.005)
    return mapping[key]


def test_old_generation_batch_does_not_reach_new_queue():
    """旧ワーカーの on_batch が呼ばれても新世代のキュー/UIに混入しない"""
    app = _make_app()
    app._library_xml_playlists = [{"Name": "PL-A"}, {"Name": "PL-B"}]

    on_batches = {}

    def fake_worker(playlist_name, batch_size, on_batch, cancel_event):
        on_batches[playlist_name] = on_batch

    app._stream_playlist_tracks_from_xml_worker = fake_worker

    app.load_tracks("PL-A")
    old_on_batch = _wait_for(on_batches, "PL-A")
    app.load_tracks("PL-B")
    new_on_batch = _wait_for(on_batches, "PL-B")

    # 旧世代Aのワーカーがキャンセル検知前にバッチを送信しようとする
    old_on_batch([_track("old-track", 1)])
    # 新世代Bのバッチ
    new_on_batch([_track("new-track", 1)])

    # 新世代のポーラーを駆動（旧キューに残ったバッチは処理されないはず）
    app._poll_track_queue(app._track_load_generation)

    assert _inserted_names(app) == ["new-track"]


def test_stale_poller_exits_without_rescheduling():
    """旧世代の _poll_track_queue は新キューを読まず after も呼ばない"""
    app = _make_app()
    app._library_xml_playlists = [{"Name": "PL-A"}, {"Name": "PL-B"}]
    app._stream_playlist_tracks_from_xml_worker = lambda *a: None

    app.load_tracks("PL-A")
    gen_a = app._track_load_generation
    app.load_tracks("PL-B")

    app.root.after.reset_mock()
    app.track_tree.insert.reset_mock()

    app._poll_track_queue(gen_a)  # 旧世代ポーラーの発火

    app.root.after.assert_not_called()
    app.track_tree.insert.assert_not_called()


def test_load_tracks_does_not_join_stale_worker():
    """旧ワーカーが生存中でも load_tracks は join で UI をブロックしない"""
    app = _make_app()
    app._library_xml_playlists = [{"Name": "PL-A"}, {"Name": "PL-B"}]

    started = threading.Event()
    release = threading.Event()

    def blocking_worker(playlist_name, batch_size, on_batch, cancel_event):
        started.set()
        while not cancel_event.is_set() and not release.is_set():
            time.sleep(0.005)

    app._stream_playlist_tracks_from_xml_worker = blocking_worker

    app.load_tracks("PL-A")
    assert started.wait(2)
    old_thread = app._track_loading_thread
    assert old_thread is not None and old_thread.is_alive()

    join_calls = []
    original_join = old_thread.join
    old_thread.join = lambda *a, **k: join_calls.append((a, k))
    try:
        app.load_tracks("PL-B")
        assert join_calls == [], f"旧ワーカーを join している: {join_calls}"
    finally:
        release.set()
        original_join(timeout=2)


def test_display_tracks_batch_sync_respects_given_cancel_event():
    """渡された世代の cancel_event がセットされていればトラックを表示しない"""
    app = _make_app()
    ev = threading.Event()
    ev.set()
    app._display_tracks_batch_sync([_track("x")], None, ev)
    app.track_tree.insert.assert_not_called()


def test_poller_reschedules_with_same_generation_while_queue_non_empty():
    """キューに残りがあれば世代を引き継いで after で再スケジュールする"""
    app = _make_app()
    gen = 7
    app._track_load_generation = gen
    q = queue.Queue()
    for i in range(5):
        q.put([_track(f"t{i}", i + 1)])
    app._track_loading_queue = q
    app._track_loading_cancel_event = threading.Event()
    app._track_loading_thread = None

    app._poll_track_queue(gen)

    # 1ティックで最大3バッチ処理し、残りは再スケジュール
    assert app.track_tree.insert.call_count == 3
    app.root.after.assert_called_once()
    args = app.root.after.call_args[0]
    assert args[0] == 30
    assert args[2] == gen  # 世代引数を引き継ぐ
