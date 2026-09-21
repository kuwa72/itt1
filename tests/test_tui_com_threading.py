"""Issue #3: キー入力スレッドから COM (self.itunes) へ直接アクセスしないことのテスト。

keyboard グローバルフックのコールバックと msvcrt ポーリングスレッドは
キーイベントを queue に積むだけにし、COM 操作を伴う handle_key_press は
メインスレッド (= self.itunes を生成した STA) の run() ループで処理する。

あわせて itunes_controller の _last_result_* キャッシュが _cache_lock 配下で
読み書きされることを検証する。

rich / keyboard / msvcrt / win32com / pythoncom は conftest.py の stub 経由。
"""

import ast
import inspect
import queue
import textwrap
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

import itunes_controller
import tui_interface
from itunes_controller import iTunesController
from tui_interface import iTunesTUI


@pytest.fixture
def tui() -> iTunesTUI:
    """iTunesController/ConfigManager を差し替えて実際の __init__ を通す。"""
    with patch.object(
        tui_interface, "iTunesController", MagicMock(name="iTunesController")
    ), patch.object(tui_interface, "ConfigManager") as cm_cls:
        cm = cm_cls.return_value
        cm.get_quick_slots.return_value = []
        cm.check_library_xml_exists.return_value = ("", False)
        cm.config.skip_seconds = 10
        app = iTunesTUI()
    app.itunes.get_playlists.return_value = []
    app.handle_key_press = MagicMock(name="handle_key_press")
    return app


def _run_main_loop_once(app: iTunesTUI, **patch_kwargs) -> None:
    """run() のメインループを1イテレーションで抜けるよう依存を差し替えて実行する。"""
    app.update_track_info = MagicMock(
        name="update_track_info",
        side_effect=lambda: setattr(app, "running", False),
    )
    with patch.object(tui_interface.time, "sleep"):
        app.run()


# --- __init__ ---

def test_init_creates_key_event_queue(tui):
    """キーイベント受け渡し用の queue.Queue を保持している"""
    assert isinstance(tui._key_event_queue, queue.Queue)


# --- keyboard グローバルフック側のコールバック ---

def test_global_key_hook_enqueues_without_dispatch(tui):
    """フックコールバックは handle_key_press を呼ばずキューに (name, ctrl) を積む"""
    event = SimpleNamespace(name="q")
    with patch.object(
        tui_interface.keyboard, "is_pressed", return_value=True
    ) as is_pressed:
        ret = tui._on_global_key_press(event)

    assert ret is False  # イベント消費
    is_pressed.assert_called_once_with("ctrl")
    assert tui._key_event_queue.get_nowait() == ("q", True)
    tui.handle_key_press.assert_not_called()


def test_global_key_hook_ignores_ctrl_key_itself(tui):
    """Ctrl 単体の押下はキューに積まない"""
    for name in ("ctrl", "left ctrl", "right ctrl"):
        assert tui._on_global_key_press(SimpleNamespace(name=name)) is False
    assert tui._key_event_queue.empty()
    tui.handle_key_press.assert_not_called()


def test_global_key_hook_tolerates_is_pressed_error(tui):
    """is_pressed が例外を投げても ctrl=False としてキューに積む"""
    with patch.object(
        tui_interface.keyboard, "is_pressed", side_effect=RuntimeError("hook err")
    ):
        ret = tui._on_global_key_press(SimpleNamespace(name="a"))
    assert ret is False
    assert tui._key_event_queue.get_nowait() == ("a", False)
    tui.handle_key_press.assert_not_called()


# --- msvcrt ローカルリーダスレッド ---

def _run_local_key_reader_once(app: iTunesTUI, getwch_values) -> None:
    """kbhit が1回 True を返した後に running を落としてループを抜ける。"""
    app.running = True

    def _stop_after_sleep(_sec):
        app.running = False

    with patch.object(
        tui_interface.msvcrt, "kbhit", side_effect=[True, False]
    ), patch.object(
        tui_interface.msvcrt, "getwch", side_effect=getwch_values
    ), patch.object(
        tui_interface.time, "sleep", side_effect=_stop_after_sleep
    ):
        app._local_key_reader()


def test_local_key_reader_enqueues_normal_key(tui):
    _run_local_key_reader_once(tui, ["x"])
    assert tui._key_event_queue.get_nowait() == ("x", False)
    tui.handle_key_press.assert_not_called()


def test_local_key_reader_enqueues_space_as_space(tui):
    _run_local_key_reader_once(tui, [" "])
    assert tui._key_event_queue.get_nowait() == ("space", False)
    tui.handle_key_press.assert_not_called()


def test_local_key_reader_enqueues_special_key(tui):
    """矢印キー (0xe0 + コード) はマップ済みの名前でキューに積む"""
    _run_local_key_reader_once(tui, ["\xe0", "H"])  # ↑
    assert tui._key_event_queue.get_nowait() == ("up", False)
    tui.handle_key_press.assert_not_called()


def test_local_key_reader_ignores_unbound_key(tui):
    """key_bindings 外のキーは従来通り無視する"""
    _run_local_key_reader_once(tui, ["1"])  # slot key はローカルリーダでは対象外
    assert tui._key_event_queue.empty()
    tui.handle_key_press.assert_not_called()


# --- メインスレッド側の drain ---

def test_drain_key_events_dispatches_in_order(tui):
    """キューのイベントが FIFO で handle_key_press に届く"""
    tui._key_event_queue.put(("x", False))
    tui._key_event_queue.put(("q", True))

    tui._drain_key_events()

    assert tui.handle_key_press.call_args_list == [
        call("x", ctrl=False),
        call("q", ctrl=True),
    ]
    assert tui._key_event_queue.empty()


def test_drain_key_events_continues_after_handler_error(tui):
    """1件のハンドラ例外で残りのイベント処理が止まらない"""
    tui.handle_key_press.side_effect = [RuntimeError("boom"), None]
    tui._key_event_queue.put(("x", False))
    tui._key_event_queue.put(("z", False))

    tui._drain_key_events()

    assert tui.handle_key_press.call_count == 2
    assert tui._key_event_queue.empty()


# --- run() 統合 ---

def test_run_dispatches_queued_events_on_main_thread(tui):
    """run() のメインループがキューを drain して handle_key_press を呼ぶ"""
    tui._key_event_queue.put(("x", False))
    with patch.object(
        tui_interface.keyboard, "on_press"
    ) as on_press, patch.object(
        tui_interface.threading, "Thread"
    ) as thread_cls:
        _run_main_loop_once(tui)

    on_press.assert_called_once()
    thread_cls.return_value.start.assert_called_once()
    tui.handle_key_press.assert_called_once_with("x", ctrl=False)


def test_run_hook_callback_only_enqueues(tui):
    """run() が keyboard.on_press に渡すコールバックはキュー投入のみ行う"""
    captured = {}

    def _capture(cb):
        captured["cb"] = cb

    with patch.object(
        tui_interface.keyboard, "on_press", side_effect=_capture
    ), patch.object(tui_interface.threading, "Thread"):
        _run_main_loop_once(tui)

    cb = captured["cb"]
    with patch.object(tui_interface.keyboard, "is_pressed", return_value=False):
        assert cb(SimpleNamespace(name="x")) is False
    assert tui._key_event_queue.get_nowait() == ("x", False)
    tui.handle_key_press.assert_not_called()


def test_run_passes_local_key_reader_to_thread(tui):
    """msvcrt フォールバックスレッドは _local_key_reader を daemon で起動する"""
    with patch.object(
        tui_interface.keyboard, "on_press"
    ), patch.object(
        tui_interface.threading, "Thread"
    ) as thread_cls:
        _run_main_loop_once(tui)

    assert thread_cls.call_args.kwargs.get("daemon") is True
    target = thread_cls.call_args.kwargs.get(
        "target", thread_cls.call_args.args[0] if thread_cls.call_args.args else None
    )
    assert target == tui._local_key_reader


# --- itunes_controller: _last_result_* のロック保護 ---

def _make_controller() -> iTunesController:
    ctrl = iTunesController.__new__(iTunesController)
    ctrl.itunes = MagicMock(name="itunes")
    ctrl.current_track = None
    ctrl._last_result_sig = None
    ctrl._last_result_names = []
    ctrl._last_result_time = 0.0
    ctrl._playlist_dbid_cache = {}
    ctrl._cache_ttl_sec = 300.0
    ctrl._cache_lock = threading.Lock()
    ctrl._recent_sig_cache = {}
    ctrl._recent_sig_ttl_sec = 300.0
    return ctrl


class _RecordingLock:
    """with 経由の acquire 回数を記録するロックラッパー。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.enter_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self._lock.__enter__()

    def __exit__(self, *args):
        return self._lock.__exit__(*args)

    def acquire(self, *args, **kwargs):
        return self._lock.acquire(*args, **kwargs)

    def release(self):
        return self._lock.release()


def test_last_result_cache_access_is_inside_cache_lock():
    """get_playlists_of_current_track_threadsafe 内の self._last_result_* アクセスは
    すべて `with self._cache_lock:` ブロック内にあること（構造チェック）"""
    src = textwrap.dedent(
        inspect.getsource(iTunesController.get_playlists_of_current_track_threadsafe)
    )
    tree = ast.parse(src)

    locked_ranges = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            expr = item.context_expr
            if (
                isinstance(expr, ast.Attribute)
                and expr.attr == "_cache_lock"
                and isinstance(expr.value, ast.Name)
                and expr.value.id == "self"
            ):
                locked_ranges.append((node.body[0].lineno, node.body[-1].end_lineno))

    accesses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr.startswith("_last_result_")
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ]
    assert accesses, "self._last_result_* アクセスが見つからない"
    for node in accesses:
        assert any(lo <= node.lineno <= hi for lo, hi in locked_ranges), (
            f"line {node.lineno}: self.{node.attr} が _cache_lock 外でアクセスされている"
        )


def _fake_itunes_for_threadsafe(track=None, source_count=0):
    it = SimpleNamespace(CurrentTrack=track)
    if track is not None:
        it.Sources = SimpleNamespace(Count=source_count)
    return it


_TRACK = SimpleNamespace(
    TrackDatabaseID=1, Name="n", Artist="a", Album="b", Duration=100
)


def test_last_result_read_is_under_lock():
    """キャッシュヒット時の _last_result_* 読み取りがロック取得を伴う"""
    ctrl = _make_controller()
    lock = _RecordingLock()
    ctrl._cache_lock = lock
    sig = ("n", "a", "b", 100)
    ctrl._last_result_sig = sig
    ctrl._last_result_names = ["P1"]
    ctrl._last_result_time = time.time()

    it = _fake_itunes_for_threadsafe(track=_TRACK)
    with patch.object(itunes_controller.win32com.client, "Dispatch", return_value=it):
        result = ctrl.get_playlists_of_current_track_threadsafe()

    assert result == ["P1"]
    assert lock.enter_count >= 1


def test_last_result_write_is_under_lock():
    """キャッシュミス時の _last_result_* 書き込みがロック取得を伴う"""
    ctrl = _make_controller()
    lock = _RecordingLock()
    ctrl._cache_lock = lock

    it = _fake_itunes_for_threadsafe(track=_TRACK, source_count=0)
    with patch.object(itunes_controller.win32com.client, "Dispatch", return_value=it):
        result = ctrl.get_playlists_of_current_track_threadsafe()

    assert result == []
    assert ctrl._last_result_sig == ("n", "a", "b", 100)
    assert ctrl._last_result_names == []
    # read 判定 + recent_sig 参照 + write で計3回の acquire を期待
    # (修正前は recent_sig の1回のみ)
    assert lock.enter_count >= 3
