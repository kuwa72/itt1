"""Issue #5: UIスレッドでのブロッキング処理排除のテスト。

- ライブラリXMLの plistlib.load をUIスレッドで実行しない（ワーカーへ委譲）
- シークバーのドラッグはデバウンスし、COM呼出しはワーカー経由で1回だけ
- update_ui_loop は get_current_track_info を直接呼ばずキュー経由で反映
- on_track_select / on_playlist_select の再生COM呼出しはUIスレッドをブロックしない
- load_playlists / pick_slots の全件走査はキャッシュ/バックグラウンド経由

tkinter は conftest.py のフェイクモジュール経由。
ITunesTkApp は __init__ を通さず __new__ + 必要属性の代入で生成する。
"""

import plistlib
import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import gui_tk
from gui_tk import ITunesTkApp
from music_controller_windows import WindowsMusicController


def _make_app() -> ITunesTkApp:
    """COMワーカースレッドは起動しないテスト用インスタンス。
    キューだけ用意し、タスクの積み上がりと _execute_com_task の実行を個別に検証する。
    """
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.root = MagicMock(name="root")
    app.root.focus_get.return_value = None
    app.ctrl = MagicMock(name="ctrl")
    app.ctrl.get_current_track_info.return_value = {}
    app.config = MagicMock(name="config")
    app.config.config.refresh_interval = 0.5
    # update_ui_loop が触るウィジェット群
    app.track_status = MagicMock(name="track_status")
    app.track_title = MagicMock(name="track_title")
    app.track_artist = MagicMock(name="track_artist")
    app.track_album = MagicMock(name="track_album")
    app.track_time = MagicMock(name="track_time")
    app.progress_bar = MagicMock(name="progress_bar")
    app.bpm_label = MagicMock(name="bpm_label")
    app.playlist_listbox = MagicMock(name="playlist_listbox")
    app.bpm_value = None
    app.last_action = "-"
    app._seeking = False
    app._synced_dbid = None
    app._synced_playlist = None
    app._update_loop_failures = 0
    app._closing = False
    app._modal_open = 0
    app._pending_after_ids = set()
    app._playlist_click_after_id = None
    app._manual_playlist_view = False
    app._last_playlist_play_at = 0.0
    app.quick_slots = []
    # トラック読み込みワーカー管理
    app._track_loading_thread = None
    app._track_loading_cancel_event = None
    app._track_loading_queue = None
    app._track_loading_playlist = None
    app._track_loading_dbid = None
    app._track_load_generation = 0
    app._track_tree_item_meta = {}
    app.track_tree = MagicMock(name="track_tree")
    app.track_tree.get_children.return_value = ()
    # ライブラリXMLまわり
    app._library_xml_path = ""
    app._library_xml_exists = False
    app._library_xml_mtime = None
    app._library_xml_tracks = None
    app._library_xml_playlists = None
    app._library_xml_lock = threading.Lock()
    app._library_xml_loader_thread = None
    app._playlist_raw_names = []
    # COMワーカー / UIキュー（ワーカースレッド自体は起動しない）
    app._ui_queue = queue.Queue()
    app._com_task_queue = queue.Queue()
    app._com_worker_stop = threading.Event()
    app._com_worker_thread = None
    app._latest_track_info = None
    # シークデバウンス
    app._seek_pending_value = None
    app._seek_after_id = None
    # 音量スライダー（Issue #25）
    app.volume_scale = MagicMock(name="volume_scale")
    app.volume_label = MagicMock(name="volume_label")
    app._volume_seeking = False
    app._volume_pending_value = None
    app._volume_after_id = None
    return app


# --- 1. ライブラリXML読込のバックグラウンド化 ---

def test_load_tracks_does_not_parse_xml_on_ui_thread(tmp_path):
    """キャッシュ未取得でも load_tracks はUIスレッドで plistlib.load を呼ばず、
    実際の解析はバックグラウンドワーカー側で行われる"""
    app = _make_app()
    xml = tmp_path / "Library.xml"
    with open(xml, "wb") as f:
        plistlib.dump(
            {"Tracks": {}, "Playlists": [{"Name": "PL", "Playlist Items": []}]}, f
        )
    app._library_xml_path = str(xml)
    app._library_xml_exists = True

    main_thread = threading.current_thread()
    call_threads = []

    def recording_load(fp):
        call_threads.append(threading.current_thread())
        return {"Tracks": {}, "Playlists": [{"Name": "PL", "Playlist Items": []}]}

    with patch.object(gui_tk.plistlib, "load", side_effect=recording_load):
        app.load_tracks("PL")
        # ワーカーは実スレッドで起動するため完了まで待機
        if app._track_loading_thread is not None:
            app._track_loading_thread.join(timeout=5)

    assert all(t is not main_thread for t in call_threads), (
        "plistlib.load がUIスレッドで呼ばれた"
    )


def test_get_playlist_folder_map_does_not_parse_on_ui_thread():
    """_get_playlist_folder_map はキャッシュのみ参照し、未取得時は解析せず
    バックグラウンド読込を要求するだけにする"""
    app = _make_app()
    app._library_xml_path = "/nonexistent/Library.xml"  # stat失敗 → キャッシュ無効
    app._load_library_xml_if_needed = MagicMock(return_value=True)
    app._ensure_library_xml_load_started = MagicMock(name="ensure_loader")

    result = app._get_playlist_folder_map()

    assert result == {}
    app._load_library_xml_if_needed.assert_not_called()
    app._ensure_library_xml_load_started.assert_called_once()


def test_get_playlist_folder_map_reads_valid_cache(tmp_path):
    """キャッシュ有効時はロック内のプレイリスト情報からフォルダマップを構築する"""
    app = _make_app()
    xml = tmp_path / "Library.xml"
    xml.write_bytes(b"<plist/>")
    app._library_xml_path = str(xml)
    import os
    app._library_xml_mtime = float(os.stat(xml).st_mtime)
    app._library_xml_tracks = {}
    app._library_xml_playlists = [
        {"Name": "Folder", "Playlist Persistent ID": "F1"},
        {"Name": "Child", "Playlist Persistent ID": "C1", "Parent Persistent ID": "F1"},
    ]
    app._load_library_xml_if_needed = MagicMock(return_value=True)

    folder_map = app._get_playlist_folder_map()

    assert folder_map == {"Child": "Folder"}
    app._load_library_xml_if_needed.assert_not_called()


def test_xml_worker_reports_load_failure_as_error_batch():
    """XML読込失敗時、ワーカーはエラーバッチをキューへ送る（静かに終わらない）"""
    app = _make_app()
    app._load_library_xml_if_needed = lambda: False

    batches = []
    app._stream_playlist_tracks_from_xml_worker("PL", 50, batches.append, None)

    assert len(batches) == 1
    assert batches[0][0].get(gui_tk.XML_ERROR_KEY)


def test_xml_worker_reports_missing_playlist_as_error_batch():
    """XML内にプレイリストが無い場合もエラーバッチを送出する"""
    app = _make_app()
    app._load_library_xml_if_needed = lambda: True
    with app._library_xml_lock:
        app._library_xml_tracks = {}
        app._library_xml_playlists = [{"Name": "Other"}]

    batches = []
    app._stream_playlist_tracks_from_xml_worker("PL", 50, batches.append, None)

    assert len(batches) == 1
    assert "PL" in batches[0][0][gui_tk.XML_ERROR_KEY]


def test_display_tracks_batch_handles_error_sentinel():
    """エラーバッチはトラック挿入せず last_action とエラー表示に回す"""
    app = _make_app()
    app._show_error = MagicMock(name="_show_error")

    app._display_tracks_batch_sync([{gui_tk.XML_ERROR_KEY: "XML読込失敗"}], None, None)

    app.track_tree.insert.assert_not_called()
    app._show_error.assert_called_once_with("XML読込失敗")
    assert "XML読込失敗" in app.last_action


# --- 2. シークバーのデバウンス ---

def test_on_progress_change_debounces_and_offloads_seek():
    """ドラッグ中の連続イベントはデバウンスされ、COM呼出しはワーカー経由で1回だけ"""
    app = _make_app()
    app._seeking = True
    app._latest_track_info = {"duration": 200.0}

    for v in (10, 20, 30, 40, 50):
        app.on_progress_change(v)

    # UIスレッドではCOMを呼ばない
    app.ctrl.set_player_position.assert_not_called()
    app.ctrl.get_current_track_info.assert_not_called()
    # 先行スケジュールはキャンセルされ、最後の1発だけが残る
    assert app.root.after_cancel.call_count == 4
    assert app.root.after.call_args[0][0] == app.SEEK_DEBOUNCE_MS

    # デバウンスコールバック発火 → COMタスクとしてキューに積まれる
    app._flush_pending_seek()
    task = app._com_task_queue.get_nowait()
    app._execute_com_task(app.ctrl, task)
    app.ctrl.set_player_position.assert_called_once_with(100.0)  # 50% * 200s


def test_flush_pending_seek_is_idempotent():
    """発火済みのデバウンスコールバックが残っていても二重シークしない"""
    app = _make_app()
    app._latest_track_info = {"duration": 100.0}
    app._seek_pending_value = 25

    app._flush_pending_seek()
    app._flush_pending_seek()  # pending値は消費済み → 何もしない

    assert app._com_task_queue.qsize() == 1


def test_on_progress_change_ignores_when_not_seeking():
    """ドラッグ中でなければ何もしない（プログラムによる set 時の誤発火防止）"""
    app = _make_app()
    app._seeking = False
    app._latest_track_info = {"duration": 100.0}

    app.on_progress_change(50)

    app.root.after.assert_not_called()
    app.ctrl.set_player_position.assert_not_called()


# --- 2b. シークバーのクリック位置ジャンプ（Issue #24） ---

def _fake_event(x):
    return MagicMock(name="event", x=x)


def test_progress_bar_press_seeks_to_click_position():
    """トラフクリックは固定ステップではなく event.x/幅 の割合へジャンプする"""
    app = _make_app()
    app.progress_bar.winfo_width.return_value = 200
    app._latest_track_info = {"duration": 200.0}

    result = app._on_progress_press(_fake_event(50))  # 25%位置

    # デフォルトの「1ページ移動」を抑制しつつシーク操作を開始する
    assert result == "break"
    assert app._seeking is True
    # スライダーをクリック位置へ動かし、デバウンス経路でシークをスケジュール
    app.progress_bar.set.assert_called_once_with(25.0)
    assert app._seek_pending_value == 25.0
    app.root.after.assert_called_once()
    assert app.root.after.call_args[0][0] == app.SEEK_DEBOUNCE_MS

    # デバウンス発火 → 曲長 200s の 25% = 50s へ COM ワーカー経由でシーク
    app._flush_pending_seek()
    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert fn == "set_player_position"
    assert args == (50.0,)


def test_progress_bar_press_clips_position_to_ends():
    """event.x がバー範囲外でも 0%〜100% にクリップする"""
    app = _make_app()
    app.progress_bar.winfo_width.return_value = 200
    app._latest_track_info = {"duration": 100.0}

    app._on_progress_press(_fake_event(-20))
    app.progress_bar.set.assert_called_with(0.0)
    assert app._seek_pending_value == 0.0

    app._on_progress_press(_fake_event(999))
    app.progress_bar.set.assert_called_with(100.0)
    assert app._seek_pending_value == 100.0


def test_progress_bar_press_ignores_zero_width():
    """未レイアウト（幅0）では値を変えず、デフォルト動作だけ抑制する"""
    app = _make_app()
    app.progress_bar.winfo_width.return_value = 0

    result = app._on_progress_press(_fake_event(10))

    assert result == "break"
    assert app._seeking is True
    app.progress_bar.set.assert_not_called()
    app.root.after.assert_not_called()


def test_progress_bar_drag_follows_mouse_position():
    """B1-Motion ドラッグ中も event.x 基準のデバウンスシークを発行する"""
    app = _make_app()
    app.progress_bar.winfo_width.return_value = 200
    app._seeking = True
    app._latest_track_info = {"duration": 200.0}

    result = app._on_progress_drag(_fake_event(150))  # 75%位置

    assert result == "break"
    app.progress_bar.set.assert_called_with(75.0)
    assert app._seek_pending_value == 75.0


def test_progress_bar_release_clears_seeking_flag():
    """ボタン解放でドラッグ中フラグを落とす（進捗の自動追従を再開させる）"""
    app = _make_app()
    app._seeking = True

    app._on_progress_release(_fake_event(0))

    assert app._seeking is False


# --- 3. update_ui_loop のバックグラウンドポーリング化 ---

def test_update_ui_loop_does_not_call_get_current_track_info():
    """update_ui_loop はUIスレッドでCOMを直接呼ばない"""
    app = _make_app()

    app.update_ui_loop()

    app.ctrl.get_current_track_info.assert_not_called()
    app.root.after.assert_called_once()  # 再スケジュールは維持


def test_update_ui_loop_applies_track_info_from_queue():
    """ワーカーが _ui_queue に積んだトラック情報をウィジェットへ反映する"""
    app = _make_app()
    app._ui_queue.put(("track_info", {
        "is_playing": True, "name": "n", "artist": "a", "album": "al",
        "position": 10, "duration": 100, "dbid": 42, "playlist": "PL",
    }))
    app._on_track_changed = MagicMock(name="_on_track_changed")

    app.update_ui_loop()

    app.track_status.configure.assert_called_with(text="▶ 再生中")
    app.track_title.configure.assert_called_with(text="曲名: n")
    app.track_artist.configure.assert_called_with(text="アーティスト: a")
    app._on_track_changed.assert_called_once_with("PL", 42)
    assert app._latest_track_info["dbid"] == 42


def test_com_worker_polls_track_info_and_stops_cleanly():
    """COMワーカーはタイムアウト時に get_current_track_info をポーリングし、
    停止イベントでループを抜けてワーカー用コントローラーを close する"""
    app = _make_app()
    worker_ctrl = MagicMock(name="worker_ctrl")
    worker_ctrl.get_current_track_info.return_value = {"dbid": 7}
    worker_ctrl.get_volume.return_value = 60  # Issue #25: 音量もポーリングに同梱
    app.ctrl.create_worker_controller.return_value = worker_ctrl

    def get_then_stop(timeout=None):
        app._com_worker_stop.set()
        raise queue.Empty

    app._com_task_queue.get = get_then_stop

    app._com_worker_loop()

    app.ctrl.create_worker_controller.assert_called_once()
    worker_ctrl.get_current_track_info.assert_called_once()
    assert app._ui_queue.get_nowait() == ("track_info", {"dbid": 7, "volume": 60})
    # ワーカー専用接続は使い終わったら close される
    worker_ctrl.close.assert_called_once()


def test_com_worker_executes_task_and_posts_result():
    """COMワーカーは投入タスクを実行し、result_tag 付きなら結果をUIキューへ返す"""
    app = _make_app()
    worker_ctrl = MagicMock(name="worker_ctrl")
    worker_ctrl.get_playlists.return_value = [{"name": "A"}]
    app.ctrl.create_worker_controller.return_value = worker_ctrl

    tasks = [("get_playlists", (), {}, "playlists")]

    def get_once(timeout=None):
        if tasks:
            return tasks.pop(0)
        app._com_worker_stop.set()
        raise queue.Empty

    app._com_task_queue.get = get_once
    app._com_worker_loop()

    worker_ctrl.get_playlists.assert_called_once()
    assert app._ui_queue.get_nowait() == ("task_result", "playlists", [{"name": "A"}])


def test_windows_controller_create_worker_controller():
    """ワーカー用コントローラーは独立した接続を持つ新規インスタンス"""
    import pythoncom
    ctrl = WindowsMusicController.__new__(WindowsMusicController)
    pythoncom.CoInitialize.reset_mock()

    worker = ctrl.create_worker_controller()

    assert isinstance(worker, WindowsMusicController)
    assert worker is not ctrl
    pythoncom.CoInitialize.assert_called()


# --- 4. 再生系COM呼出しのオフロード ---

def _setup_track_tree(app):
    app.track_tree.selection.return_value = ("iid1",)

    def _item(iid, key):
        if key == "tags":
            return ("dbid:123", "pid:PID1", "src:1", "pl:2", "tid:99", "po:5", "loc:C:/x.mp3")
        return "Song"

    app.track_tree.item.side_effect = _item
    app._track_loading_playlist = "PL"


def test_on_track_select_offloads_play_to_com_worker():
    """ダブルクリック再生はUIスレッドで play_track_by_ids を呼ばずキューへ"""
    app = _make_app()
    _setup_track_tree(app)

    app.on_track_select(None)

    app.ctrl.play_track_by_ids.assert_not_called()
    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert fn == "play_track_by_ids"
    assert args == (1, 2, 99, 123, "PID1", "Song", "PL", 5)

    # ワーカー実行 → 結果がUIキュー経由で last_action に反映される
    app.ctrl.play_track_by_ids.return_value = True
    app._execute_com_task(app.ctrl, (fn, args, kwargs, tag))
    app.ctrl.play_track_by_ids.assert_called_once_with(*args)
    app._drain_ui_queue()
    assert app.last_action == "トラック再生: Song"


def test_on_track_select_reports_failure_via_result_tag():
    app = _make_app()
    _setup_track_tree(app)

    app.on_track_select(None)

    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    app.ctrl.play_track_by_ids.return_value = False
    app._execute_com_task(app.ctrl, (fn, args, kwargs, tag))
    app._drain_ui_queue()
    assert app.last_action == "トラック再生失敗"


def test_on_playlist_select_offloads_play_playlist():
    """プレイリスト再生もUIスレッドでCOMを呼ばない"""
    app = _make_app()
    app.playlist_listbox.curselection.return_value = (0,)
    app._playlist_raw_names = ["PL-A"]

    app.on_playlist_select(None)

    app.ctrl.play_playlist.assert_not_called()
    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert (fn, args) == ("play_playlist", ("PL-A",))


# --- 5. プレイリスト一覧の非同期化 ---

def test_load_playlists_submits_task_and_applies_result():
    """load_playlists はUIスレッドで get_playlists を呼ばず、結果はキュー経由で反映"""
    app = _make_app()
    app._get_playlist_folder_map = lambda: {}

    app.load_playlists()

    app.ctrl.get_playlists.assert_not_called()
    fn, args, kwargs, tag = app._com_task_queue.get_nowait()
    assert fn == "get_playlists"
    assert tag == "playlists"

    app.ctrl.get_playlists.return_value = [{"name": "A"}, {"name": "B"}]
    app._execute_com_task(app.ctrl, (fn, args, kwargs, tag))
    app._drain_ui_queue()

    assert app._playlist_raw_names == ["A", "B"]
    assert app.playlist_listbox.insert.call_count == 2


def test_pick_slots_uses_cached_playlist_names():
    """pick_slots はUIスレッドで get_all_playlists を全件走査せずキャッシュを使う"""
    app = _make_app()
    app._playlist_raw_names = ["PL-A", "PL-B"]
    app._get_playlist_folder_map = lambda: {}
    app.quick_slots = []
    fake_dlg = MagicMock(name="dlg")
    fake_dlg.result = None

    with patch("gui_tk.PlaylistPicker", return_value=fake_dlg):
        app.pick_slots()

    app.ctrl.get_all_playlists.assert_not_called()
    app.root.wait_window.assert_called_once_with(fake_dlg)


# --- 6. 終了処理との整合 ---

def test_on_closing_stops_com_worker():
    """on_closing はCOMワーカーを停止イベント＋停止トークンで止め、短く join する"""
    app = _make_app()
    worker = MagicMock(name="com_worker")
    worker.is_alive.return_value = True
    app._com_worker_thread = worker

    app.on_closing()

    assert app._com_worker_stop.is_set()
    assert app._com_task_queue.get_nowait() is gui_tk._COM_STOP
    worker.join.assert_called_once()
    timeout = worker.join.call_args.kwargs.get(
        "timeout", worker.join.call_args.args[0] if worker.join.call_args.args else None
    )
    assert timeout is not None and timeout <= 1.0


def test_com_submit_drops_task_when_closing():
    """終了処理中のCOMタスク投入は捨てる"""
    app = _make_app()
    app._closing = True

    app._com_submit("play_pause")

    assert app._com_task_queue.empty()
