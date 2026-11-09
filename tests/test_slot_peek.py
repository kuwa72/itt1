"""Issue #49: Shift ホールドで裏バンクのスロットを peek 表示するテスト。

- Shift 押下中は裏バンク（1 - slot_bank）のラベルを表示
- Shift 解放で現在バンクへ復帰
- peek は表示のみ変更し slot_bank（実際の追加対象）は変えない
- モーダル中・終了処理中は抑制
"""

from collections import deque
from unittest.mock import MagicMock

from gui_tk import ITunesTkApp


def _make_app(bank=0):
    app = ITunesTkApp.__new__(ITunesTkApp)
    app.slot_bank = bank
    app.bank_size = 3
    app.slot_keys = list("123")
    app.quick_slots = ["A1", "A2", "A3", "B1", "B2", "B3"]
    app.slot_labels = [MagicMock(name=f"lbl{i}") for i in range(3)]
    app.slots_page_label = MagicMock(name="page")
    app._slot_default_bg = "#000"
    app._slot_default_fg = "#fff"
    app._slot_empty_fg = "#888"
    app._peeking_bank = None
    app._closing = False
    app._modal_open = 0
    app.last_action = ""
    app._action_log = deque(maxlen=50)
    app.action_log_listbox = None
    app._ui_queue = None
    return app


def _texts(app):
    return [lbl.configure.call_args[1]["text"] for lbl in app.slot_labels]


class TestPeekBank:
    def test_peek_shows_other_bank(self):
        """Shift 押下で裏バンク（bank 1）のスロット名が見える"""
        app = _make_app(bank=0)

        app._peek_slot_bank()

        assert _texts(app) == ["1\nB1", "2\nB2", "3\nB3"]
        assert app._peeking_bank == 1

    def test_release_restores_current_bank(self):
        """Shift 解放で現在バンクの表示に戻る"""
        app = _make_app(bank=0)
        app._peek_slot_bank()

        app._unpeek_slot_bank()

        assert _texts(app) == ["1\nA1", "2\nA2", "3\nA3"]
        assert app._peeking_bank is None

    def test_peek_from_bank1_shows_bank0(self):
        """バンク1表示中の peek はバンク0を見せる"""
        app = _make_app(bank=1)

        app._peek_slot_bank()

        assert _texts(app) == ["1\nA1", "2\nA2", "3\nA3"]

    def test_peek_does_not_change_slot_bank(self):
        """peek は表示のみ。slot_bank（追加対象の決定値）は変えない"""
        app = _make_app(bank=0)

        app._peek_slot_bank()

        assert app.slot_bank == 0

    def test_release_without_peek_is_noop(self):
        """peek していない状態での Shift 解放は何もしない"""
        app = _make_app(bank=0)
        for lbl in app.slot_labels:
            lbl.reset_mock()

        app._unpeek_slot_bank()

        for lbl in app.slot_labels:
            lbl.configure.assert_not_called()

    def test_peek_suppressed_while_modal(self):
        app = _make_app(bank=0)
        app._modal_open = 1

        app._peek_slot_bank()

        assert app._peeking_bank is None

    def test_peek_suppressed_while_closing(self):
        app = _make_app(bank=0)
        app._closing = True

        app._peek_slot_bank()

        assert app._peeking_bank is None

    def test_peek_updates_page_label_to_other_bank(self):
        """peek 中のバンク表示も裏バンクの番号を示す"""
        app = _make_app(bank=0)

        app._peek_slot_bank()

        app.slots_page_label.configure.assert_called_with(text="バンク: 2/2")


class TestUpdateSlotLabelsBankArg:
    def test_update_slot_labels_with_explicit_bank(self):
        """update_slot_labels(bank=N) で指定バンクの内容を描画する"""
        app = _make_app(bank=0)

        app.update_slot_labels(bank=1)

        assert _texts(app) == ["1\nB1", "2\nB2", "3\nB3"]

    def test_update_slot_labels_default_is_current_bank(self):
        app = _make_app(bank=1)

        app.update_slot_labels()

        assert _texts(app) == ["1\nB1", "2\nB2", "3\nB3"]
