"""Issue #3: キー入力スレッドから COM (self.itunes) へ直接アクセスしないことのテスト。

keyboard グローバルフックのコールバックと msvcrt ポーリングスレッドは
キーイベントを queue に積むだけにし、COM 操作を伴う handle_key_press は
メインスレッド (= self.itunes を生成した STA) の run() ループで処理する。

itunes_controller は Issue #7 で共通コントローラー (create_music_controller) に
統合され削除済みのため、旧 _last_result_* キャッシュのロック検証テストも削除した。

rich / keyboard / msvcrt / win32com / pythoncom は conftest.py の stub 経由。
"""

import queue
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

import tui_interface
from tui_interface import iTunesTUI


@pytest.fixture
def tui() -> iTunesTUI:
    """共通コントローラー/ConfigManager を差し替えて実際の __init__ を通す。"""
    with patch.object(
        tui_interface, "create_music_controller", MagicMock(name="create_music_controller")
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
