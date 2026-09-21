"""Issue #17: 最終アクション表示のログ表示枠化テスト。

- _log_action(msg) が self.last_action を更新し、タイムスタンプ付きで履歴へ追記する
- 履歴は ACTION_LOG_MAX_ENTRIES 件に制限され、古い行から削除される
- ログ用 Listbox (action_log_listbox) があれば末尾へ追記・先頭から削除される
- UIスレッド以外からの呼出しは _ui_queue 経由でUIスレッドへ回される
- last_action_label は削除済みで、self.last_action への直接代入は _log_action 内のみ
"""

import ast
import queue
import re
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import tkinter as tk
from gui_tk import ITunesTkApp

ROOT = Path(__file__).resolve().parent.parent


def _make_app(with_listbox: bool = False) -> ITunesTkApp:
    """__init__ を通さず _log_action に必要な最小構成で生成する"""
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.last_action = "-"
    app._ui_queue = queue.Queue()
    if with_listbox:
        app.action_log_listbox = MagicMock(name="action_log_listbox")
        app.action_log_listbox.size.return_value = 0
    return app


# --- _log_action の基本動作 ---

def test_log_action_sets_last_action():
    app = _make_app()
    app._log_action("再生/一時停止")
    assert app.last_action == "再生/一時停止"


def test_log_action_appends_timestamped_entry():
    app = _make_app()
    app._log_action("次の曲")
    assert len(app._action_log) == 1
    entry = app._action_log[0]
    assert re.match(r"^\d{2}:\d{2}:\d{2} ", entry), f"タイムスタンプ形式でない: {entry}"
    assert entry.endswith("次の曲")


def test_log_action_keeps_history_newest_last():
    app = _make_app()
    app._log_action("1件目")
    app._log_action("2件目")
    app._log_action("3件目")
    assert len(app._action_log) == 3
    assert app._action_log[0].endswith("1件目")
    assert app._action_log[-1].endswith("3件目")
    assert app.last_action == "3件目"


def test_log_action_trims_to_max_entries():
    app = _make_app()
    limit = ITunesTkApp.ACTION_LOG_MAX_ENTRIES
    for i in range(limit + 10):
        app._log_action(f"action-{i}")
    assert len(app._action_log) == limit
    # 先頭10件は捨てられ、最新が末尾に残る
    assert app._action_log[-1].endswith(f"action-{limit + 9}")
    assert app._action_log[0].endswith("action-10")


def test_log_action_works_without_widget_or_queue():
    """__new__ 直後の最小状態（_ui_queue 無し）でも例外なく記録できる"""
    app = ITunesTkApp.__new__(ITunesTkApp)
    app._log_action("起動")
    assert app.last_action == "起動"
    assert len(app._action_log) == 1


# --- ログ用 Listbox との同期 ---

def test_log_action_inserts_into_listbox():
    app = _make_app(with_listbox=True)
    app._log_action("追加: Foo")
    app.action_log_listbox.insert.assert_called_once()
    args = app.action_log_listbox.insert.call_args[0]
    assert args[0] == tk.END
    assert args[1].endswith("追加: Foo")
    app.action_log_listbox.see.assert_called_once_with(tk.END)


def test_log_action_deletes_oldest_listbox_row_when_full():
    app = _make_app(with_listbox=True)
    limit = ITunesTkApp.ACTION_LOG_MAX_ENTRIES
    # 1回だけ上限超過 → delete(0) が1回呼ばれて収まる
    app.action_log_listbox.size.side_effect = [limit + 1, limit]
    app._log_action("overflow")
    app.action_log_listbox.delete.assert_called_once_with(0)


def test_log_action_survives_listbox_error():
    """Listbox 側の例外が _log_action の呼出し元へ伝播しない"""
    app = _make_app(with_listbox=True)
    app.action_log_listbox.insert.side_effect = RuntimeError("tk error")
    app._log_action("ウィジェット失敗")  # 例外が漏れないこと
    assert app.last_action == "ウィジェット失敗"
    assert len(app._action_log) == 1


# --- 非UIスレッドからの呼出し ---

def test_log_action_from_worker_thread_enqueues_to_ui_queue():
    """ワーカースレッドから呼ばれた場合はTkに触れず _ui_queue へ回す"""
    app = _make_app()
    t = threading.Thread(target=app._log_action, args=("worker-msg",))
    t.start()
    t.join(timeout=5)
    kind, payload = app._ui_queue.get_nowait()
    assert kind == "log"
    assert payload == "worker-msg"


def test_drain_ui_queue_applies_log_message():
    """("log", msg) が _drain_ui_queue で _log_action に反映される"""
    app = _make_app()
    app._latest_track_info = None
    app._ui_queue.put(("log", "ドレイン経由"))
    app._drain_ui_queue()
    assert app.last_action == "ドレイン経由"
    assert app._action_log[-1].endswith("ドレイン経由")


# --- 構造（ソースレベル） ---

def _gui_source() -> str:
    return (ROOT / "gui_tk.py").read_text(encoding="utf-8")


def test_last_action_label_removed():
    """トラック情報パネル内の last_action_label はログ枠へ置き換わり削除済み"""
    assert "last_action_label" not in _gui_source()


def test_log_frame_and_listbox_exist_in_source():
    src = _gui_source()
    assert "action_log_listbox" in src


def test_last_action_assigned_only_inside_log_action():
    """self.last_action への直接代入は _log_action 内に集約されている"""
    tree = ast.parse(_gui_source())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == "_log_action":
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "self"
                and sub.attr == "last_action"
                and isinstance(sub.ctx, ast.Store)
            ):
                offenders.append(f"{node.name}:{sub.lineno}")
    assert not offenders, f"_log_action 以外での self.last_action 代入: {offenders}"
