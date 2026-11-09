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


# --- Issue #53: peek 中の直接追加 + JIS/US レイアウト ---

from types import SimpleNamespace


def _make_app_us(bank=0):
    """実際の US スロットキー列を使うアプリ"""
    app = _make_app(bank=bank)
    app.root = MagicMock(name="root")
    app.root.focus_get.return_value = None
    app.slot_keys = list(ITunesTkApp.SLOT_KEY_ROWS["us"])
    app.bank_size = len(app.slot_keys)
    app.quick_slots = [f"P{i}" for i in range(72)]
    app._unshift_map = ITunesTkApp.SLOT_UNSHIFT["us"]
    app.keyboard_layout = "us"
    app.add_to_slot = MagicMock(name="add_to_slot")
    return app


def _ev(keysym, char="", state=0, keycode=0):
    return SimpleNamespace(keysym=keysym, char=char, state=state, keycode=keycode)


class TestPeekAdd:
    def test_peek_add_letter(self):
        """peek 中に Shift+Q → 裏バンクの q 位置へ追加"""
        app = _make_app_us(bank=0)
        app._peeking_bank = 1

        app.on_key(_ev("Q", char="Q", state=0x1, keycode=0x51))

        app.add_to_slot.assert_called_once_with(36 + 12)  # 'q' は12番目

    def test_peek_add_digit_via_unshift(self):
        """US: Shift+1(char='!') → 裏バンクの 1 位置へ"""
        app = _make_app_us(bank=0)
        app._peeking_bank = 1

        app.on_key(_ev("exclam", char="!", state=0x1, keycode=0x31))

        app.add_to_slot.assert_called_once_with(36 + 0)

    def test_peek_add_symbol_via_unshift(self):
        """US: Shift+=(char='+') → 裏バンクの = 位置へ"""
        app = _make_app_us(bank=0)
        app._peeking_bank = 1

        app.on_key(_ev("plus", char="+", state=0x1, keycode=0xBB))

        app.add_to_slot.assert_called_once_with(36 + 11)  # '=' は11番目

    def test_peek_add_via_keycode_fallback(self):
        """文字が出ないキー（JIS Shift+0等）→ keycode で物理キー判定"""
        app = _make_app_us(bank=0)
        app._peeking_bank = 1

        app.on_key(_ev("Shift_0", char="", state=0x1, keycode=0x30))

        app.add_to_slot.assert_called_once_with(36 + 9)  # '0' は9番目

    def test_no_add_when_not_peeking(self):
        """peek していない状態の Shift+Q は何もしない"""
        app = _make_app_us(bank=0)

        app.on_key(_ev("Q", char="Q", state=0x1, keycode=0x51))

        app.add_to_slot.assert_not_called()

    def test_peek_add_does_not_change_bank(self):
        app = _make_app_us(bank=0)
        app._peeking_bank = 1

        app.on_key(_ev("Q", char="Q", state=0x1, keycode=0x51))

        assert app.slot_bank == 0
        assert app._peeking_bank == 1


class TestJISLayout:
    def _make_jis(self):
        app = _make_app_us(bank=0)
        app.keyboard_layout = "jis"
        app.slot_keys = list(ITunesTkApp.SLOT_KEY_ROWS["jis"])
        app._unshift_map = ITunesTkApp.SLOT_UNSHIFT["jis"]
        return app

    def test_jis_at_key_adds_slot(self):
        """JIS: @ キー（char='@'）でスロット位置22へ追加"""
        app = self._make_jis()

        app.on_key(_ev("at", char="@", state=0, keycode=0xC0))

        app.add_to_slot.assert_called_once_with(12 + 10)  # qwerty行の'@'位置

    def test_jis_colon_key_adds_slot(self):
        """JIS: : キーでスロット位置へ追加"""
        app = self._make_jis()

        app.on_key(_ev("colon", char=":", state=0, keycode=0xBA))

        # jis rows: "1234567890-^"(12) + "qwertyuiop@["(12) + "asdfghjkl;:]"(12)
        # ':' は3行目 index 10 → 24+10 = 34
        app.add_to_slot.assert_called_once_with(34)

    def test_jis_peek_add_shift2(self):
        """JIS peek: Shift+2(char='"') → 裏バンクの 2 位置へ"""
        app = self._make_jis()
        app._peeking_bank = 1

        app.on_key(_ev("quotedbl", char='"', state=0x1, keycode=0x32))

        app.add_to_slot.assert_called_once_with(36 + 1)

    def test_jis_peek_add_asterisk_to_colon(self):
        """JIS peek: Shift+:(char='*') → 裏バンクの : 位置へ"""
        app = self._make_jis()
        app._peeking_bank = 1

        app.on_key(_ev("asterisk", char="*", state=0x1, keycode=0xBA))

        app.add_to_slot.assert_called_once_with(36 + 34)


class TestLayoutSwitch:
    def test_apply_layout_rebuilds_keys_and_labels(self):
        app = _make_app_us(bank=0)
        app._kb_layout_var = MagicMock()
        app._kb_layout_var.get.return_value = "jis"
        app.config = MagicMock(name="config")

        app._apply_keyboard_layout()

        assert app.keyboard_layout == "jis"
        assert app.slot_keys == list(ITunesTkApp.SLOT_KEY_ROWS["jis"])
        assert app._unshift_map == ITunesTkApp.SLOT_UNSHIFT["jis"]
        app.config.set_keyboard_layout.assert_called_once_with("jis")
        # ラベルが JIS 文字に更新される
        texts = [lbl.configure.call_args[1]["text"] for lbl in app.slot_labels]
        assert texts[0].startswith("1\n")

    def test_invalid_layout_falls_back_to_us(self):
        app = _make_app_us(bank=0)
        app._kb_layout_var = MagicMock()
        app._kb_layout_var.get.return_value = "dvorak"
        app.config = MagicMock(name="config")

        app._apply_keyboard_layout()

        assert app.keyboard_layout == "us"


class TestKeycodeToSlotKey:
    def test_digit_and_letter(self):
        assert ITunesTkApp._keycode_to_slot_key(0x30) == "0"
        assert ITunesTkApp._keycode_to_slot_key(0x39) == "9"
        assert ITunesTkApp._keycode_to_slot_key(0x41) == "a"
        assert ITunesTkApp._keycode_to_slot_key(0x5A) == "z"

    def test_non_alnum_returns_none(self):
        assert ITunesTkApp._keycode_to_slot_key(0xBD) is None  # OEM_MINUS
        assert ITunesTkApp._keycode_to_slot_key(0x10) is None  # VK_SHIFT
        assert ITunesTkApp._keycode_to_slot_key(0) is None
